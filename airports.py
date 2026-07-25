"""Risoluzione aeroporti: città, paese e fascia (corto/lungo raggio).

Usa il dataset `airportsdata` per i codici IATA aeroportuali; i codici
metropolitani (LON, PAR, ...) usati da Travelpayouts sono mappati a mano.
"""

import airportsdata

_AIRPORTS = airportsdata.load("IATA")

# Codici città (metro area) che non esistono nel dataset aeroporti.
_CITY_CODES: dict[str, tuple[str, str]] = {
    "LON": ("Londra", "GB"),
    "PAR": ("Parigi", "FR"),
    "ROM": ("Roma", "IT"),
    "MIL": ("Milano", "IT"),
    "STO": ("Stoccolma", "SE"),
    "BUH": ("Bucarest", "RO"),
    "NYC": ("New York", "US"),
    "TYO": ("Tokyo", "JP"),
    "MOW": ("Mosca", "RU"),
    "SAO": ("San Paolo", "BR"),
    "RIO": ("Rio de Janeiro", "BR"),
    "BJS": ("Pechino", "CN"),
    "SHA": ("Shanghai", "CN"),
    "SEL": ("Seul", "KR"),
    "WAS": ("Washington", "US"),
    "CHI": ("Chicago", "US"),
}

# Paesi in fascia "corto raggio" per le soglie di prezzo: Europa geografica
# più le mete nordafricane/mediorientali servite dalle low-cost europee.
_SHORT_HAUL_COUNTRIES = {
    "AL", "AD", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK",
    "EE", "ES", "FI", "FR", "GB", "GG", "GI", "GR", "HR", "HU", "IE", "IM",
    "IS", "IT", "JE", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT",
    "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "SM", "UA", "VA",
    "XK", "RU", "TR", "MA", "TN", "DZ", "EG", "IL", "JO", "GE", "AM", "AZ",
}


# Codici che servono la stessa area metropolitana. Il confronto sui nomi non
# basta (MIL="Milano" ma MXP="Milan", ROM="Roma" ma FCO="Rome"), e serve per
# non proporre una "tappa" a due passi da casa né due tappe nella stessa città.
_METRO_GROUPS: list[set[str]] = [
    {"MIL", "MXP", "LIN", "BGY"},
    {"ROM", "FCO", "CIA"},
    {"VCE", "TSF"},
    {"LON", "LHR", "LGW", "STN", "LTN", "LCY", "SEN"},
    {"PAR", "CDG", "ORY", "BVA"},
    {"BER", "SXF", "TXL"},
    {"STO", "ARN", "BMA", "NYO", "VST"},
    {"OSL", "TRF"},
    {"BRU", "CRL"},
    {"BUH", "OTP", "BBU"},
    {"MOW", "SVO", "DME", "VKO"},
    {"IST", "SAW"},
    {"NYC", "JFK", "EWR", "LGA"},
    {"WAS", "IAD", "DCA", "BWI"},
    {"CHI", "ORD", "MDW"},
    {"SAO", "GRU", "CGH", "VCP"},
    {"RIO", "GIG", "SDU"},
    {"TYO", "NRT", "HND"},
    {"BJS", "PEK", "PKX"},
    {"SHA", "PVG"},
    {"SEL", "ICN", "GMP"},
]
_METRO_OF: dict[str, int] = {
    code: index for index, group in enumerate(_METRO_GROUPS) for code in group
}


def same_metro(a: str, b: str) -> bool:
    """True se i due codici servono la stessa città/area metropolitana."""
    a, b = a.upper(), b.upper()
    if a == b:
        return True
    group_a, group_b = _METRO_OF.get(a), _METRO_OF.get(b)
    return group_a is not None and group_a == group_b


def info(iata: str) -> tuple[str, str]:
    """Ritorna (nome città, codice paese ISO). Fallback: (codice IATA, "")."""
    iata = iata.upper()
    if iata in _CITY_CODES:
        return _CITY_CODES[iata]
    airport = _AIRPORTS.get(iata)
    if airport:
        return airport.get("city") or airport.get("name") or iata, airport.get("country", "")
    return iata, ""


def is_known(iata: str) -> bool:
    """True se il codice IATA esiste nel dataset (aeroporto o città metro)."""
    iata = iata.upper()
    return iata in _CITY_CODES or iata in _AIRPORTS


def is_short_haul(iata: str) -> bool:
    """True se la destinazione è in fascia Europa/corto raggio.

    Le destinazioni sconosciute sono trattate come corto raggio: si applica
    la soglia più bassa, quindi l'errore è conservativo (meno falsi affari).
    """
    _, country = info(iata)
    return country in _SHORT_HAUL_COUNTRIES if country else True
