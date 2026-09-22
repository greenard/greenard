import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import fr from "./fr.json";

const saved = (() => {
  try {
    return localStorage.getItem("greenard.lang");
  } catch {
    return null;
  }
})();

void i18n.use(initReactI18next).init({
  resources: { fr: { translation: fr }, en: { translation: en } },
  lng: saved === "en" ? "en" : "fr",
  fallbackLng: "fr",
  interpolation: { escapeValue: false },
});

export function setLanguage(lng: "fr" | "en") {
  void i18n.changeLanguage(lng);
  document.documentElement.lang = lng;
  try {
    localStorage.setItem("greenard.lang", lng);
  } catch {
    /* stockage indisponible */
  }
}

export default i18n;
