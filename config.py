import os

AZIENDA = {
    "ragione_sociale": "SHI MEIYE",
    "indirizzo": "Via Vigne di Spagna snc, 12032 Barge (CN)",
    "partita_iva": "04136510049",
    "codice_fiscale": "SHIMYE66H54Z210G"
}

# Legge la password da Render in produzione, o usa il fallback in locale
PASSWORD_ACCESSO = os.environ.get("ACCESS_PASSWORD", "hu123")

BANCHE = {
    "BPER": {
        "id": "BPER",
        "nome": "BPER Banca di Luserna San Giovanni - IT35S0538730600000004332185",
        "label_pdf": "BPER Banca di Luserna San Giovanni - IBAN IT35S0538730600000004332185"
    },
    "POSTE": {
        "id": "POSTE",
        "nome": "Poste Italiane - IT04B0760110200001078221247",
        "label_pdf": "Poste Italiane - IBAN IT04B0760110200001078221247"
    }