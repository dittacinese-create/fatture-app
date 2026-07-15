import os

AZIENDA = {
    "ragione_sociale": "SHI MEIYE",
    "indirizzo": "Via Vigne di Spagna snc, 12032 Barge (CN)",
    "partita_iva": "04136510049",
    "codice_fiscale": "SHIMYE66H54Z210G"
}

# Legge la password da Render in produzione, o usa il fallback in locale
PASSWORD_ACCESSO = os.environ.get("ACCESS_PASSWORD", "hu123")