MYNETA_BASE = "https://myneta.info"

# Most recent state assembly election per state.
# ministers_url: state govt council-of-ministers page; None = skip minister tagging.
STATE_PIPELINES = [
    {"state_id": 1,  "name": "Andhra Pradesh",     "myneta_slug": "andhrapradesh2024",   "ministers_url": None},
    {"state_id": 2,  "name": "Arunachal Pradesh",  "myneta_slug": "arunachalpradesh2024","ministers_url": None},
    {"state_id": 3,  "name": "Assam",              "myneta_slug": "assam2021",           "ministers_url": None},
    {"state_id": 4,  "name": "Bihar",              "myneta_slug": "bihar2020",           "ministers_url": None},
    {"state_id": 5,  "name": "Chhattisgarh",       "myneta_slug": "chhattisgarh2023",    "ministers_url": None},
    {"state_id": 6,  "name": "Goa",                "myneta_slug": "goa2022",             "ministers_url": None},
    {"state_id": 7,  "name": "Gujarat",            "myneta_slug": "gujarat2022",         "ministers_url": None},
    {"state_id": 8,  "name": "Haryana",            "myneta_slug": "haryana2024",         "ministers_url": None},
    {"state_id": 9,  "name": "Himachal Pradesh",   "myneta_slug": "himachalpradesh2022", "ministers_url": None},
    {"state_id": 10, "name": "Jharkhand",          "myneta_slug": "jharkhand2024",       "ministers_url": None},
    {"state_id": 11, "name": "Karnataka",          "myneta_slug": "karnataka2023",       "ministers_url": None},
    {"state_id": 12, "name": "Kerala",             "myneta_slug": "kerala2021",          "ministers_url": None},
    {"state_id": 13, "name": "Madhya Pradesh",     "myneta_slug": "madhyapradesh2023",   "ministers_url": None},
    {"state_id": 14, "name": "Maharashtra",        "myneta_slug": "maharashtra2024",     "ministers_url": None},
    {"state_id": 15, "name": "Manipur",            "myneta_slug": "manipur2022",         "ministers_url": None},
    {"state_id": 16, "name": "Meghalaya",          "myneta_slug": "meghalaya2023",       "ministers_url": None},
    {"state_id": 17, "name": "Mizoram",            "myneta_slug": "mizoram2023",         "ministers_url": None},
    {"state_id": 18, "name": "Nagaland",           "myneta_slug": "nagaland2023",        "ministers_url": None},
    {"state_id": 19, "name": "Odisha",             "myneta_slug": "odisha2024",          "ministers_url": None},
    {"state_id": 20, "name": "Punjab",             "myneta_slug": "punjab2022",          "ministers_url": None},
    {"state_id": 21, "name": "Rajasthan",          "myneta_slug": "rajasthan2023",       "ministers_url": None},
    {"state_id": 22, "name": "Sikkim",             "myneta_slug": "sikkim2024",          "ministers_url": None},
    {"state_id": 23, "name": "Tamil Nadu",         "myneta_slug": "tamilnadu2021",       "ministers_url": None},
    {"state_id": 24, "name": "Telangana",          "myneta_slug": "telangana2023",       "ministers_url": "https://telangana.gov.in/government/council-of-ministers/"},
    {"state_id": 25, "name": "Tripura",            "myneta_slug": "tripura2023",         "ministers_url": None},
    {"state_id": 26, "name": "Uttar Pradesh",      "myneta_slug": "uttarpradesh2022",    "ministers_url": None},
    {"state_id": 27, "name": "Uttarakhand",        "myneta_slug": "uttarakhand2022",     "ministers_url": None},
    {"state_id": 28, "name": "West Bengal",        "myneta_slug": "westbengal2021",      "ministers_url": None},
    {"state_id": 29, "name": "Delhi",              "myneta_slug": "delhi2025",           "ministers_url": None},
    {"state_id": 30, "name": "Jammu & Kashmir",    "myneta_slug": "jammukashmir2024",    "ministers_url": None},
]

# Fallback slugs tried in order if the primary slug returns 0 constituencies
FALLBACK_SLUGS: dict[int, list[str]] = {
    29: ["delhi2020"],   # Delhi 2025 may not be indexed yet
    30: ["jk2024"],      # J&K alternate slug
    2:  ["arunachal2024"],
    9:  ["hp2022"],
}
