"""Category knowledge learned from past File Exchange uploads.

Every id here has been accepted by eBay on real listings. The AI picks
one of these keys when it fits; anything else goes through eBay's category
suggestions (needs API keys). FALLBACK_ASPECTS lists the item specifics each
category is known to require — used when the Taxonomy API isn't configured.
"""

CATEGORIES = {
    # key: (category id, name, default package size)
    "tcg_card":        ("183454", "CCG Individual Cards (Pokémon, One Piece, Naruto…)", "card"),
    "sports_card":     ("261328", "Sports Trading Card Singles", "card"),
    "ccg_sealed":      ("261044", "CCG Sealed Boxes & Packs (ETBs, booster boxes)", "medium"),
    "video_game":      ("139973", "Video Games", "small"),
    "funko":           ("149372", "Funko Pop! Vinyl Figures", "small"),
    "action_figure":   ("261068", "Action Figures & Accessories", "small"),
    "doll":            ("262346", "Dolls", "small"),
    "plush":           ("230", "Stuffed Animals / Plush", "small"),
    "jellycat":        ("158786", "Jellycat", "small"),
    "lego":            ("19006", "LEGO Complete Sets & Packs", "medium"),
    "diecast":         ("180506", "Diecast & Toy Vehicles", "small"),
    "headset":         ("171821", "Headsets", "small"),
    "drinkware":       ("177006", "Vacuum Flasks & Mugs (YETI, Stanley)", "medium"),
    "smartwatch":      ("178893", "Smart Watches", "small"),
    "security_camera": ("185055", "Security Cameras", "small"),
    "music_cd":        ("176984", "Music CDs", "small"),
}

BY_ID = {cid: (key, name) for key, (cid, name, _) in CATEGORIES.items()}

CARD_CATEGORY_IDS = {"183454", "261328", "183455"}

# Categories that only take New (1000) / Used (3000) — learned from error 21916883.
NEW_USED_ONLY = {"261068", "149372", "262346", "230", "19006", "180506", "177006",
                 "171821", "158786", "178893"}

# Required item specifics per category (from error 21919303 fixes). Values
# here are defaults; None means "must come from the item".
FALLBACK_ASPECTS = {
    "183454": {"Game": None},
    "261328": {"Sport": "Baseball"},
    "261044": {"Game": None, "Set": None},
    "139973": {"Platform": None, "Game Name": None},
    "149372": {"Type": "Pop!"},
    "261068": {"Type": "Action Figure"},
    "262346": {"Type": "Doll"},
    "230": {"Type": "Plush"},
    "158786": {"Type": "Plush"},
    "19006": {"Type": "Complete Set"},
    "180506": {"Type": "Car"},
    "171821": {"Type": "Headset", "Connectivity": None, "Color": None, "Model": None},
    "177006": {"Type": "Tumbler", "Color": None},
    "178893": {"Type": "Smart Watch", "Model": None, "Color": None,
               "Compatible Operating System": None, "Band Material": None, "Case Size": None},
    "185055": {"Type": None, "Model": None},
    "176984": {"Artist": None, "Format": "CD", "Release Title": None},
}

# Graded-card condition descriptor ids (Inventory API + File Exchange).
GRADER_IDS = {"PSA": "275010", "BCCG": "275011", "BVG": "275012", "BGS": "275013",
              "CGC": "275015", "SGC": "275016"}
GRADE_IDS = {"10": "275020", "9.5": "275021", "9": "275022", "8.5": "275023", "8": "275024",
             "7.5": "275025", "7": "275026", "6.5": "275027", "6": "275028", "5.5": "275029",
             "5": "2750210", "4.5": "2750211", "4": "2750212", "3.5": "2750213", "3": "2750214",
             "2.5": "2750215", "2": "2750216", "1.5": "2750217", "1": "2750218"}
UNGRADED_CARD_DESCRIPTOR = {"40001": "400010"}  # Card Condition: Near mint or better

CONDITION_NAMES = {
    "1000": "New", "1500": "New other (open box)", "1750": "New with defects",
    "2750": "Like New / Graded", "3000": "Used", "4000": "Very Good / Ungraded",
    "5000": "Good", "6000": "Acceptable", "7000": "For parts / not working",
}


def is_card(category_id):
    return str(category_id or "") in CARD_CATEGORY_IDS
