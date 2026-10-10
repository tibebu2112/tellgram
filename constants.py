"""Shared labels for the BDU Student Union complaint system."""

from __future__ import annotations

CAMPUSES = [
    "ፔዳ (Peda)",
    "ፖሊ / BiT",
    "ዘንዘልማ (Zenzelma)",
    "ሰባታሚት (Sebatamit)",
    "ጥቁር ዓባይ / ጥበበ ጊዮን (Tibebe Ghion)",
    "ይባቤ (Yibabe)",
    "ሰላም (Selam)",
]

CATEGORIES = [
    "🍽 የካፌ አገልግሎት",
    "🏠 የዶርም አገልግሎት",
    "📚 አካዳሚክና ቴክኖሎጂ",
    "🛡 ሰላምና ደህንነት",
    "💡 ሌሎች ቅሬታዎች",
]

DEPARTMENTS = [
    "Computer Science",
    "Information Systems",
    "Electrical Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Water / Hydraulics",
    "Agriculture",
    "Medicine / Health",
    "Business / Economics",
    "Law",
    "Education",
    "Other",
]

STATUSES = ["Open", "In Progress", "Resolved"]
PRIORITIES = ["High", "Medium", "Low"]

STATUS_AM = {
    "Open": "🔴 አልተፈታም",
    "In Progress": "🟡 በሂደት ላይ",
    "Resolved": "🟢 ተፈትቷል",
}

PRIORITY_AM = {
    "High": "High / አጣዳፊ",
    "Medium": "Medium",
    "Low": "Low",
}

IDENTITY_NAMED = "👤 ስሜ ይገለጽ"
IDENTITY_ANON = "🕵 በምስጢር ይያዝ"
SKIP = "⏭ ዝለል (Skip)"
NEW_COMPLAINT = "➕ ሌላ ቅሬታ"

DEFAULT_ACTIONS = {
    "🍽 የካፌ አገልግሎት": "ከካፌ አስተዳደር ጋር መነጋገር",
    "🏠 የዶርም አገልግሎት": "ለዶርም አስተዳደር መላክ",
    "📚 አካዳሚክና ቴክኖሎጂ": "ለአይሲቲ / አካዳሚክ ክፍሉ መላክ",
    "🛡 ሰላምና ደህንነት": "ለካምፓስ ጥበቃ መላክ",
    "💡 ሌሎች ቅሬታዎች": "ለህብረት ጽ/ቤት መላክ",
}

CAMPUS_COORDS = {
    "ፔዳ (Peda)": (11.5975, 37.3954),
    "ፖሊ / BiT": (11.5740, 37.3615),
    "ዘንዘልማ (Zenzelma)": (11.6320, 37.3780),
    "ሰባታሚት (Sebatamit)": (11.5480, 37.4180),
    "ጥቁር ዓባይ / ጥበበ ጊዮን (Tibebe Ghion)": (11.5890, 37.3830),
    "ይባቤ (Yibabe)": (11.6050, 37.4100),
    "ሰላም (Selam)": (11.5850, 37.4050),
}


def infer_priority(category: str, description: str) -> str:
    blob = f"{category} {description}".lower()
    if category.startswith("🛡"):
        return "High"
    high_markers = (
        "ስጋት",
        "ጥቃት",
        "ደህንነት",
        "emergency",
        "ውሃ የለም",
        "መብራት የለም",
        "fire",
        "wifi የለም",
        "wi-fi የለም",
    )
    if any(marker in blob for marker in high_markers):
        return "High"
    if category.startswith(("🍽", "🏠", "📚")):
        return "Medium"
    return "Low"


def default_action(category: str) -> str:
    return DEFAULT_ACTIONS.get(category, "ለህብረት ጽ/ቤት መላክ")
