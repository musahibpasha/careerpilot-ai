import { getApp, getApps, initializeApp } from "firebase/app";
import { browserLocalPersistence, getAuth, setPersistence } from "firebase/auth";

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY?.trim() ?? "",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN?.trim() ?? "",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID?.trim() ?? "",
  appId: import.meta.env.VITE_FIREBASE_APP_ID?.trim() ?? "",
};

export const firebaseConfigured = Object.values(config).every(Boolean);

const firebaseApp = firebaseConfigured
  ? getApps().length
    ? getApp()
    : initializeApp(config)
  : null;

export const firebaseAuth = firebaseApp ? getAuth(firebaseApp) : null;

export const persistenceReady = firebaseAuth
  ? setPersistence(firebaseAuth, browserLocalPersistence)
  : Promise.resolve();
