import json
import requests
import urllib.parse
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline, TextStreamer
import googlemaps
import polyline
import matplotlib.pyplot as plt
import io
import base64
from fpdf import FPDF
from datetime import datetime
import random
import os
import math
import torch

FOURSQUARE_API_KEY = os.getenv('FOURSQUARE_API_KEY')
OPENCAGE_API_KEY = os.getenv('OPENCAGE_API_KEY')
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')
NEWS_API_KEY = os.getenv('NEWS_API_KEY')
FOURSQUARE_ENDPOINT = os.getenv('FOURSQUARE_ENDPOINT')
OPENCAGE_ENDPOINT = os.getenv('OPENCAGE_ENDPOINT')
NEWS_API_ENDPOINT = os.getenv('NEWS_API_ENDPOINT')
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

class ModelInitializer:
    def __init__(self):
        self.model_name_or_path = "TheBloke/Mistral-7B-Instruct-v0.2-AWQ"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name_or_path)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name_or_path, low_cpu_mem_usage=True, device_map="auto")
        self.generation_params = {
            "do_sample": True,
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 40,
            "max_new_tokens": 4096,
            "repetition_penalty": 1.1
        }
        self.pipe = pipeline("text-generation", model=self.model, tokenizer=self.tokenizer, **self.generation_params, return_full_text=False)
        self.streamer = TextStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)

    def get_output(self, input_text):
        prompt_template = f'''<s>[INST] {input_text} [/INST]'''
        tokens = self.tokenizer(prompt_template, return_tensors='pt').input_ids.cuda()
        pipe_output = self.pipe(prompt_template)[0]['generated_text']
        return pipe_output

class PlaceManager:
    def __init__(self):
        self.gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    def fetch_coordinates(self, place_name):
        params = {
            'q': place_name,
            'key': OPENCAGE_API_KEY
        }
        response = requests.get(OPENCAGE_ENDPOINT, params=params)
        data = response.json()

        if data['results']:
            result = data['results'][0]
            lat = result['geometry']['lat']
            lng = result['geometry']['lng']
            return lat, lng
        else:
            raise ValueError("No coordinates found for the given place name.")

    def fetch_places(self, lat, lng, radius, min_price, max_price, sort_by, limit):
        headers = {
            'Authorization': FOURSQUARE_API_KEY,
        }
        params = {
            'll': f'{lat},{lng}',
            'radius': radius,
            'price': f"{min_price},{max_price}",
            'sort': sort_by,
            'limit': limit,
        }
        response = requests.get(FOURSQUARE_ENDPOINT, headers=headers, params=params)
        data = response.json()

        with open('foursquare_data.json', 'w') as f:
            json.dump(data, f, indent=4)

        return data

    def get_directions(self, origin, destination, mode='driving', waypoints=None):
        directions_result = self.gmaps.directions(origin, destination, mode=mode, departure_time='now', waypoints=waypoints)
        if directions_result:
            route = directions_result[0]
            legs = route['legs']
            total_distance = sum(leg['distance']['value'] for leg in legs)
            total_duration = sum(leg['duration']['value'] for leg in legs)

            steps = []
            for leg in legs:
                steps.extend(leg['steps'])

            directions_summary = {
                'origin': origin,
                'destination': destination,
                'distance': f"{total_distance / 1000:.2f} km",
                'duration': f"{total_duration // 60} minutes",
                'steps': [],
                'polyline': route['overview_polyline']['points']
            }

            for step in steps:
                directions_summary['steps'].append({
                    'instruction': step['html_instructions'],
                    'distance': step['distance']['text'],
                    'duration': step['duration']['text']
                })

            return directions_summary
        else:
            return None

class ItineraryGenerator:
    def __init__(self, model_initializer):
        self.model = model_initializer

    def summarize_place(self, place_name, place_data):
        description = place_data.get('description', 'No description available')
        if description == 'No description available':
            name = place_data.get('name', 'Unknown Place')
            address = place_data.get('location', {}).get('formatted_address', 'Address not available')
            categories = ', '.join(cat.get('name', 'Unknown') for cat in place_data.get('categories', []))
            description = f"{name} is located at {address}. It is known for its {categories}."
        else:
            description = description[:500] + '...'

        prompt = f"Summarize the following description of {place_name} in 3 to 4 lines: {description}. Exclude any introductory or irrelevant information. And also must be complete"
        summary = self.model.get_output(prompt)

        summary = '. '.join(summary.split('. ')[1:])
        return summary.strip() if summary else "No summary available."

    def generate_itinerary_based_on_places(self, places_data, place_name, days):
        attractions = []
        restaurants = []

        for place in places_data.get('results', []):
            categories = place.get('categories', [])
            if categories:
                category_name = categories[0].get('name', '').lower()
                if 'restaurant' not in category_name and 'cafe' not in category_name:
                    attractions.append(place)
                if 'restaurant' in category_name or 'cafe' in category_name:
                    restaurants.append(place)

        places_per_day = max(1, math.ceil(len(attractions) / days))
        itinerary_sections = []

        for day in range(1, days + 1):
            start_index = (day - 1) * places_per_day
            end_index = start_index + places_per_day
            day_attractions = attractions[start_index:end_index]

            itinerary_day = [f"Day {day}: Exploring {place_name}'s Cultural Landmarks"]

            for i, attraction in enumerate(day_attractions):
                if attraction['location']['formatted_address'].lower().startswith(place_name.lower()):
                    itinerary_day.append(f"{10 + i * 3}:00 AM - {attraction['name']}")
                    description = self.summarize_place(attraction['name'], attraction)
                    itinerary_day.append(f"Description: {description}")

                    nearby_restaurants = [restaurant for restaurant in restaurants
                                          if restaurant['location']['formatted_address'].lower().startswith(place_name.lower())]

                    if nearby_restaurants:
                        itinerary_day.append(f"Nearby Restaurants:")
                        for restaurant in nearby_restaurants[:2]:
                            itinerary_day.append(f"- {restaurant['name']} ({restaurant['location']['formatted_address']})")

            itinerary_sections.append("\n".join(itinerary_day))

        prompt = f"""
          Generate a carefully curated {days}-day itinerary for a trip to {place_name}, focusing on highly popular and well-known attractions within a 50 km range from the {place_name} center.

          For each day:
          1. Provide a detailed schedule with specific timeframes, including:
            - Breakfast (e.g., 7:30 AM - 8:30 AM)
            - Morning attraction (e.g., 9:00 AM - 11:30 AM)
            - Lunch (e.g., 12:00 PM - 1:00 PM)
            - Afternoon attraction (e.g., 1:30 PM - 4:00 PM)
            - Evening attraction (e.g., 4:30 PM - 7:00 PM)
            - Dinner (e.g., 7:30 PM - 9:00 PM)

          2. Feature exactly three main attractions per day, ensuring no repetitions throughout the itinerary.
          3. Prioritize attractions in close proximity (within 10 km) to minimize travel time.
          4. For each main attraction, provide:
            - Name and address
            - A brief description
            - Estimated duration of visit

          5. Recommend restaurants for each meal:
            - Two options for breakfast
            - Two options for lunch near the morning or afternoon attraction
            - Two options for dinner near the evening attraction
            Include for each restaurant:
            - Name and address
            - Cuisine type

          6. Suggest 2-3 additional activity options that could be substituted or added if time allows, such as:
            - Local markets or shopping areas
            - Parks or green spaces
            - Cultural events or performances
            - Scenic viewpoints
            - Historical sites

          At the end of the itinerary:
          1. Recommend 2 top-rated hotels near each of the following:
            - {place_name} nearby bus stand
            - Railway station
            - Airport

          To ensure accuracy and relevance:
          - Cross-check information with reputable internet sources.
          - Verify the existence and popularity of each attraction.
          - Confirm all listed attractions are distinct and within the 50 km range from {place_name} center.

          Leverage your advanced language capabilities and knowledge to create an unforgettable travel experience, showcasing the best of {place_name} in a concise and easily navigable format.
          """

        itinerary = self.model.get_output(prompt)
        return itinerary.strip()

    def get_place_time(self, schedule):
        system_prompt = '''System Prompt: I'll give you the Itinerary and from that you must provide me a json output consisting places along with their respective days and timings.
        The output should follow the given format:
        {
            "day1": {
                "place1": {
                    "place": "Accurate Place Name",
                    "time": "HH:MM",
                    "address": "Full accurate ADDRESS",
                    "lat": "Latitude",
                    "lng": "Longitude",
                    "Description":"2 line description",
                    "recommended_restaurants": [
                        {"name": "Restaurant Name 1", "address": "Restaurant Address 1","lat":"Latitude","lon":"Longitude"},
                        {"name": "Restaurant Name 2", "address": "Restaurant Address 2","lat":"Latitude","lon":"Longitude"}
                        ]
                    "recommended_hotels":[
                      {"name": "Hotel Name 1", "address": "Hotel Address 1","lat":"Latitude","lon":"Longitude"},
                      {"name": "Hotel Name 2", "address": "Hotel Address 2","lat":"Latitude","lon":"Longitude"}
                    ],
                },
                "place2": {
                    "place": "Place Name",
                    "time": "HH:MM",
                    "lat": "Latitude",
                    "lng": "Longitude",
                    "address": "Full accurate ADDRESS",
                    "Description":"2 line description",
                    "recommended_restaurants": [
                        {"name": "Restaurant Name 1", "address": "Restaurant Address 1","lat":"Latitude","lon":"Longitude"},
                        {"name": "Restaurant Name 2", "address": "Restaurant Address 2","lat":"Latitude","lon":"Longitude"}
                    ],
                    "recommended_hotels":[
                      {"name": "Hotel Name 1", "address": "Hotel Address 1","lat":"Latitude","lon":"Longitude"},
                      {"name": "Hotel Name 2", "address": "Hotel Address 2","lat":"Latitude","lon":"Longitude"}
                    ]
                }
            },
            "day2": {
                ...
            }
        }
        Ensure that the "address" field contains the full and accurate address for each place and restaurant.
        Schedule: '''

        place_time_output = self.model.get_output(system_prompt + schedule)

        try:
            json_start = place_time_output.index('{')
            json_end = place_time_output.rindex('}') + 1
            json_output = place_time_output[json_start:json_end]
            place_time_json = json.loads(json_output)
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Failed to parse JSON output from the model.")

        return place_time_json

class NewsFetcher:
    def fetch_latest_news(self, location_name):
        params = {
            'q': location_name,
            'apiKey': NEWS_API_KEY,
            'sortBy': 'publishedAt',
            'language': 'en'
        }
        response = requests.get(NEWS_API_ENDPOINT, params=params)
        data = response.json()

        if data['status'] == 'ok':
            articles = data.get('articles', [])
            news_items = []
            for article in articles[:5]:
                news_items.append({
                    'title': article['title'],
                    'description': article.get('description', 'No description available'),
                    'url': article['url'],
                    'publishedAt': article['publishedAt']
                })
            return news_items
        else:
            raise ValueError("Failed to fetch news.")

    def display_latest_news(self, location_name):
        try:
            news_items = self.fetch_latest_news(location_name)
            print(f"\nLatest News for {location_name}:")
            for item in news_items:
                print(f"\nTitle: {item['title']}")
                print(f"Published At: {item['publishedAt']}")
                print(f"Description: {item['description']}")
                print(f"URL: {item['url']}")
        except ValueError as e:
            print(e)

class TravelItineraryPDF(FPDF):
    def header(self):
        logo_path = "/content/VV logo.jpg"
        if os.path.exists(logo_path):
            try:
                self.image(logo_path, x=50, y=8, w=30)
            except Exception as e:
                print(f"Error adding image: {e}")
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'Travel Itinerary', 0, 1, 'C')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')

    def add_page(self, *args, **kwargs):
        super().add_page(*args, **kwargs)
        self.add_border()
        self.add_watermark()
        self.set_font("Arial", '', size=12)
        self.set_text_color(0, 0, 128)

    def add_border(self):
        self.set_draw_color(0, 0, 128)
        self.set_line_width(0.5)
        self.rect(5.0, 5.0, 200.0, 287.0)

    def add_watermark(self):
        self.set_font("Arial", 'B', size=85)
        self.set_text_color(209, 209, 209)
        self.rotate(45, 105, 148)
        text = "VOYAGE VERSE"
        text_width = self.get_string_width(text)
        self.text(120- text_width / 2, 148, text)
        self.rotate(0)

def create_pdf(itinerary, summary, urls, start_date, end_date):
    pdf = TravelItineraryPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Title
    pdf.set_font("Arial", 'B', size=18)
    pdf.set_text_color(0, 0, 128)
    pdf.cell(0, 10, "Travel Itinerary and Summary", 0, 1, 'C')
    pdf.ln(5)

    # Date range and number of days
    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    end_date = datetime.strptime(end_date, "%Y-%m-%d")
    num_days = (end_date - start_date).days + 1
    date_range = f"{start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}"
    pdf.set_font("Arial", '', size=12)
    pdf.cell(0, 10, f"Date Range: {date_range} ({num_days} days)", 0, 1)
    pdf.ln(5)

    # Itinerary
    pdf.set_font("Arial", 'B', size=14)
    pdf.cell(0, 10, "Itinerary:", 0, 1)
    pdf.set_font("Arial", '', size=12)

    for day in itinerary.split("\n\n"):
        pdf.cell(0, 10, day.split("\n")[0], 0, 1)
        pdf.set_font("Arial", '', size=12)
        pdf.set_text_color(0, 0, 128)
        for activity in day.split("\n")[1:]:
            pdf.multi_cell(0, 10, activity.strip())
        pdf.ln(5)

    pdf.add_page()
    pdf.set_font("Arial", 'B', size=14)
    pdf.set_text_color(0, 0, 128)
    pdf.cell(0, 10, "Summary:", 0, 1)
    pdf.set_font("Arial", '', size=12)
    pdf.multi_cell(0, 10, summary)
    pdf.ln(5)

    pdf.set_font("Arial", 'B', size=14)
    pdf.set_text_color(0, 0, 128)
    pdf.cell(0, 10, "Google Maps Links:", 0, 1)
    pdf.set_font("Arial", '', size=12)

    for i, (place, url) in enumerate(urls.items(), 1):
        pdf.cell(0, 10, f"{i}. " + place, 0, 1, link=url)

    pdf.set_y(-25)
    pdf.set_font("Arial", 'I', size=8)
    pdf.set_text_color(0, 0, 0)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf.cell(0, 10, f"Generated on: {current_time}", 0, 0, 'R')

    pdf.output("travel_itinerary.pdf")

def get_itinerary(place_name, days, min_price, max_price, start_date, end_date):
    model_initializer = ModelInitializer()
    print("Model Initialized Successfully.")

    place_manager = PlaceManager()

    itinerary_generator = ItineraryGenerator(model_initializer)

    news = NewsFetcher()

    lat, lng = place_manager.fetch_coordinates(place_name)
    print(f"Coordinates for {place_name}: Latitude {lat}, Longitude {lng}")

    radius_km = 50
    radius_meters = int(radius_km * 1000)
    sort_by = "rating"
    limit = 50

    news.display_latest_news(place_name)

    places_data = place_manager.fetch_places(lat, lng, radius_meters, min_price, max_price, sort_by, limit)

    itinerary = itinerary_generator.generate_itinerary_based_on_places(places_data, place_name, days)
    print("\nGenerated Itinerary:")
    print(itinerary)

    while True:
        try:
            place_time = itinerary_generator.get_place_time(itinerary)
            break
        except:
            pass
    print("\nPlaces with Day and Time Information:")

    filename = 'place_time_data.json'
    with open(filename, 'w') as json_file:
        json.dump(place_time, json_file, indent=4)
    print(f"Successfully saved place_time data to {filename}")

    urls = {}
    for day in place_time.values():
        for place_info in day.values():
            if isinstance(place_info, dict):  # Ensure place_info is a dict
                place_name = place_info.get('place')
                address = place_info.get('address', '')

                if place_name and address:
                    urls[place_name] = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(address)}"




    place_data = next((place for place in places_data.get('results', []) if place.get('name') == place_name), {})
    summary = itinerary_generator.summarize_place(place_name, place_data)
    print(f"\nSummary for {place_name}:")
    print(summary)

    pdf_path = "travel_itinerary.pdf"
    create_pdf(itinerary, summary, urls, start_date, end_date)
    print("PDF generated successfully.")

if __name__ == "__main__":
    place_name = input("Enter a location name: ")
    days = int(input("Enter the number of days for the itinerary: "))
    min_price = int(input("Enter minimum price level (1-4): "))
    max_price = int(input("Enter maximum price level (1-4): "))
    start_date = input("Enter Start date (YYYY-MM-DD): ")
    end_date = input("Enter End date (YYYY-MM-DD): ")
    get_itinerary(place_name, days, min_price, max_price, start_date, end_date)

