/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_APP_VERSION: string;
  // Add other env vars here as needed
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
