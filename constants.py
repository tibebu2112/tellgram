"""Shared labels for the BDU Student Union complaint system."""

from __future__ import annotations

CAMPUSES = [
    "ፔዳ (Peda)",
    "ፖሊ / BiT",
    "ዘንዘልማ (Zenzelma)",
    "COBE",
    "ጥቁር ዓባይ / ጥበበ ጊዮን (Tibebe Ghion)",
    "ይባቤ (Yibabe)",
    "ሰላም (Selam)",
]

DEPARTMENTS = [
    "Computer Science",
    "Software Engineering",
    "Data Science",
    "Information Systems",
    "Information Technology",
    "Electrical Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Water / Hydraulics",
    "Architecture",
    "Agriculture",
    "Medicine / Health",
    "Pharmacy",
    "Nursing",
    "Business / Economics",
    "Accounting",
    "Law",
    "Education",
    "Psychology",
    "Sociology",
    "Other",
]

# New structured categories matching the spec
CATEGORIES = [
    "🏠 Dormitory / Housing",
    "💧 Water & Sanitation",
    "💡 Electricity & Lighting",
    "🍽 Cafeteria & Food Services",
    "📚 Academic / Grading / Department",
    "🛡 Security & Safety",
    "📶 Wi-Fi & Library Services",
    "❓ Other",
]

STATUSES   = ["Pending", "In Progress", "Resolved"]
PRIORITIES = ["High", "Medium", "Low"]

STATUS_AM = {
    "Pending":     "🔴 ገና አልታየም",
    "In Progress": "🟡 በሂደት ላይ",
    "Resolved":    "🟢 ተፈትቷል",
    # legacy compatibility
    "Open":        "🔴 ገና አልታየም",
}

PRIORITY_AM = {
    "High":   "High / አጣዳፊ",
    "Medium": "Medium",
    "Low":    "Low",
}

# Inline-keyboard button labels
IDENTITY_NAMED = "👤 Reveal Identity"
IDENTITY_ANON  = "🕵 Keep Anonymous"
SKIP           = "⏭ Skip Photo/Video"
NEW_COMPLAINT  = "➕ New Complaint"

DEFAULT_ACTIONS = {
    "🏠 Dormitory / Housing":            "Forward to Dorm Administration",
    "💧 Water & Sanitation":             "Forward to Facilities Management",
    "💡 Electricity & Lighting":         "Forward to Facilities Management",
    "🍽 Cafeteria & Food Services":      "Forward to Cafeteria Management",
    "📚 Academic / Grading / Department":"Forward to Academic Affairs",
    "🛡 Security & Safety":              "Forward to Campus Security",
    "📶 Wi-Fi & Library Services":       "Forward to ICT / Library",
    "❓ Other":                           "Forward to Union Office",
}

CAMPUS_COORDS = {
    "ፔዳ (Peda)":                                    (11.5975, 37.3954),
    "ፖሊ / BiT":                                     (11.5740, 37.3615),
    "ዘንዘልማ (Zenzelma)":                            (11.6320, 37.3780),
    "ሰባታሚት (Sebatamit)":                           (11.5480, 37.4180),
    "ጥቁር ዓባይ / ጥበበ ጊዮን (Tibebe Ghion)":          (11.5890, 37.3830),
    "ይባቤ (Yibabe)":                                 (11.6050, 37.4100),
    "ሰላም (Selam)":                                  (11.5850, 37.4050),
}


def infer_priority(category: str, description: str) -> str:
    blob = f"{category} {description}".lower()
    if "security" in category.lower() or "safety" in category.lower():
        return "High"
    high_markers = (
        "emergency", "fire", "attack", "flood", "danger",
        "no water", "no power", "ስጋት", "ጥቃት", "ደህንነት",
    )
    if any(m in blob for m in high_markers):
        return "High"
    if any(w in category for w in ("Dormitory", "Water", "Electricity", "Cafeteria", "Academic")):
        return "Medium"
    return "Low"


def default_action(category: str) -> str:
    return DEFAULT_ACTIONS.get(category, "Forward to Union Office")
