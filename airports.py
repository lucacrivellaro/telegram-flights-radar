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


def info(iata: str) -> tuple[str, str]:
    """Ritorna (nome città, codice paese ISO). Fallback: (codice IATA, "")."""
    iata = iata.upper()
    if iata in _CITY_CODES:
        return _CITY_CODES[iata]
    airport = _AIRPORTS.get(iata)
    if airport:
        return airport.get("city") or airport.get("name") or iata, airport.get("country", "")
    return iata, ""


def is_short_haul(iata: str) -> bool:
    """True se la destinazione è in fascia Europa/corto raggio.

    Le destinazioni sconosciute sono trattate come corto raggio: si applica
    la soglia più bassa, quindi l'errore è conservativo (meno falsi affari).
    """
    _, country = info(iata)
    return country in _SHORT_HAUL_COUNTRIES if country else True
