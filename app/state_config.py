MYNETA_BASE = "https://myneta.info"

# Most recent state assembly election per state.
# ministers_url: state govt council-of-ministers page; None = skip minister tagging.
_WP = "https://en.wikipedia.org/wiki/"

STATE_PIPELINES = [
    {"state_id": 1,  "name": "Andhra Pradesh",    "myneta_slug": "andhrapradesh2024",    "ministers_url": _WP + "Fourth_N._Chandrababu_Naidu_ministry"},
    {"state_id": 2,  "name": "Arunachal Pradesh", "myneta_slug": "arunachalpradesh2024", "ministers_url": _WP + "Fifth_Pema_Khandu_ministry"},
    {"state_id": 3,  "name": "Assam",             "myneta_slug": "assam2026",            "ministers_url": _WP + "First_Sarma_ministry"},
    {"state_id": 4,  "name": "Bihar",             "myneta_slug": "bihar2020",            "ministers_url": _WP + "Seventh_Nitish_Kumar_ministry"},
    {"state_id": 5,  "name": "Chhattisgarh",      "myneta_slug": "chhattisgarh2023",     "ministers_url": _WP + "Sai_ministry"},
    {"state_id": 6,  "name": "Goa",               "myneta_slug": "goa2022",              "ministers_url": _WP + "Second_Pramod_Sawant_ministry"},
    {"state_id": 7,  "name": "Gujarat",           "myneta_slug": "gujarat2022",          "ministers_url": _WP + "Second_Bhupendrabhai_Patel_ministry"},
    {"state_id": 8,  "name": "Haryana",           "myneta_slug": "haryana2024",          "ministers_url": _WP + "Second_Saini_ministry"},
    {"state_id": 9,  "name": "Himachal Pradesh",  "myneta_slug": "himachalpradesh2022",  "ministers_url": _WP + "Sukhu_ministry"},
    {"state_id": 10, "name": "Jharkhand",         "myneta_slug": "jharkhand2024",        "ministers_url": _WP + "Third_Hemant_Soren_ministry"},
    {"state_id": 11, "name": "Karnataka",         "myneta_slug": "karnataka2023",        "ministers_url": _WP + "Second_Siddaramaiah_ministry"},
    {"state_id": 12, "name": "Kerala",            "myneta_slug": "kerala2026",           "ministers_url": _WP + "Second_Vijayan_ministry"},
    {"state_id": 13, "name": "Madhya Pradesh",    "myneta_slug": "madhyapradesh2023",    "ministers_url": _WP + "Mohan_Yadav_ministry"},
    {"state_id": 14, "name": "Maharashtra",       "myneta_slug": "maharashtra2024",      "ministers_url": _WP + "Third_Fadnavis_ministry"},
    {"state_id": 15, "name": "Manipur",           "myneta_slug": "manipur2022",          "ministers_url": _WP + "Second_N._Biren_Singh_ministry"},
    {"state_id": 16, "name": "Meghalaya",         "myneta_slug": "meghalaya2023",        "ministers_url": _WP + "Second_Conrad_Sangma_ministry"},
    {"state_id": 17, "name": "Mizoram",           "myneta_slug": "mizoram2023",          "ministers_url": _WP + "Lalduhoma_ministry"},
    {"state_id": 18, "name": "Nagaland",          "myneta_slug": "nagaland2023",         "ministers_url": _WP + "Fifth_Rio_ministry"},
    {"state_id": 19, "name": "Odisha",            "myneta_slug": "odisha2024",           "ministers_url": _WP + "Mohan_Charan_Majhi_ministry"},
    {"state_id": 20, "name": "Punjab",            "myneta_slug": "punjab2022",           "ministers_url": _WP + "Mann_ministry"},
    {"state_id": 21, "name": "Rajasthan",         "myneta_slug": "rajasthan2023",        "ministers_url": _WP + "Bhajan_Lal_Sharma_ministry"},
    {"state_id": 22, "name": "Sikkim",            "myneta_slug": "sikkim2024",           "ministers_url": _WP + "Second_Tamang_ministry"},
    {"state_id": 23, "name": "Tamil Nadu",        "myneta_slug": "tamilnadu2026",        "ministers_url": _WP + "M._K._Stalin_ministry"},
    {"state_id": 24, "name": "Telangana",         "myneta_slug": "telangana2023",        "ministers_url": "https://telangana.gov.in/government/council-of-ministers/"},
    {"state_id": 25, "name": "Tripura",           "myneta_slug": "tripura2023",          "ministers_url": _WP + "Second_Saha_ministry"},
    {"state_id": 26, "name": "Uttar Pradesh",     "myneta_slug": "uttarpradesh2022",     "ministers_url": _WP + "Second_Yogi_Adityanath_ministry"},
    {"state_id": 27, "name": "Uttarakhand",       "myneta_slug": "uttarakhand2022",      "ministers_url": _WP + "Second_Dhami_ministry"},
    {"state_id": 28, "name": "West Bengal",       "myneta_slug": "westbengal2026",       "ministers_url": _WP + "Third_Mamata_Banerjee_ministry"},
    {"state_id": 29, "name": "Delhi",             "myneta_slug": "delhi2025",            "ministers_url": _WP + "Rekha_Gupta_ministry"},
    {"state_id": 30, "name": "Jammu & Kashmir",   "myneta_slug": "jammukashmir2024",     "ministers_url": _WP + "Second_Omar_Abdullah_ministry"},
]

# Fallback slugs tried in order if the primary slug returns 0 constituencies
FALLBACK_SLUGS: dict[int, list[str]] = {
    29: ["delhi2020"],   # Delhi 2025 may not be indexed yet
    30: ["jk2024"],      # J&K alternate slug
    2:  ["arunachal2024"],
    9:  ["hp2022"],
}
