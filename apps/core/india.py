"""Indian states / union territories with GST state codes (used for place of supply)."""
STATES = [
    ("37", "Andhra Pradesh"), ("12", "Arunachal Pradesh"), ("18", "Assam"), ("10", "Bihar"),
    ("22", "Chhattisgarh"), ("30", "Goa"), ("24", "Gujarat"), ("06", "Haryana"), ("02", "Himachal Pradesh"),
    ("20", "Jharkhand"), ("29", "Karnataka"), ("32", "Kerala"), ("23", "Madhya Pradesh"), ("27", "Maharashtra"),
    ("14", "Manipur"), ("17", "Meghalaya"), ("15", "Mizoram"), ("13", "Nagaland"), ("21", "Odisha"),
    ("03", "Punjab"), ("08", "Rajasthan"), ("11", "Sikkim"), ("33", "Tamil Nadu"), ("36", "Telangana"),
    ("16", "Tripura"), ("09", "Uttar Pradesh"), ("05", "Uttarakhand"), ("19", "West Bengal"),
    ("35", "Andaman and Nicobar Islands"), ("04", "Chandigarh"), ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("07", "Delhi"), ("01", "Jammu and Kashmir"), ("38", "Ladakh"), ("31", "Lakshadweep"), ("34", "Puducherry"),
]
STATE_CHOICES = sorted(STATES, key=lambda s: s[1])
STATE_NAMES = dict(STATES)
