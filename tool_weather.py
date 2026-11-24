# Define the actual local Python tool
from langchain_core.tools import tool

@tool
def get_weather(location: str) -> str:
    """Mock function simulating a weather app"""
    weather_data = {
        "tokyo": "In Tokyo, it is sunny and 22 degrees celsius",
        "paris": "In Paris, it is rainy and 15 degrees celsius",
        "abu dhabi": "In Abu Dhabi, it is sunny and 30 degrees celsius"
    }
    loc_clean = location.lower().strip()
    return weather_data.get(loc_clean, f"Weather data unavailable for {location}.")