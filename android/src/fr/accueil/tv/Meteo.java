package fr.accueil.tv;

/** Codes météo WMO (Open-Meteo) → symbole et libellé en français. */
final class Meteo {
    private Meteo() {
    }

    static String symbole(int code, boolean jour) {
        if (code == 0) return jour ? "☀️" : "🌙";
        if (code <= 2) return jour ? "🌤" : "☁️";
        if (code == 3) return "☁️";
        if (code == 45 || code == 48) return "🌫";
        if (code >= 51 && code <= 67) return "🌧";
        if (code >= 71 && code <= 77) return "🌨";
        if (code >= 80 && code <= 82) return "🌦";
        if (code == 85 || code == 86) return "🌨";
        if (code >= 95) return "⛈";
        return "🌡";
    }

    static String libelle(int code) {
        switch (code) {
            case 0: return "Ciel dégagé";
            case 1: return "Peu nuageux";
            case 2: return "Partiellement nuageux";
            case 3: return "Couvert";
            case 45: case 48: return "Brouillard";
            case 51: case 53: case 55: return "Bruine";
            case 56: case 57: return "Bruine verglaçante";
            case 61: case 63: case 65: return "Pluie";
            case 66: case 67: return "Pluie verglaçante";
            case 71: case 73: case 75: return "Neige";
            case 77: return "Grésil";
            case 80: case 81: case 82: return "Averses";
            case 85: case 86: return "Averses de neige";
            case 95: return "Orage";
            case 96: case 99: return "Orage et grêle";
            default: return "";
        }
    }
}
