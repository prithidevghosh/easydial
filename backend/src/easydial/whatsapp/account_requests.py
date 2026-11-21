"""Speech context for the master agent; account intents are interpreted semantically."""

TRANSCRIPTION_PROMPT = (
    "EasyDial grocery and account-management conversation in Indian languages, Hindi, Hinglish and English. "
    "Store names: Blinkit (ब्लिंकिट), Swiggy Instamart (इंस्टामार्ट). "
    "Vocabulary: login, logout, log out, sign out, disconnect, account, लॉग आउट. "
    "Preserve what is spoken, including brands, quantities, negation and corrections. "
    "Do not invent a store name or an action."
)
