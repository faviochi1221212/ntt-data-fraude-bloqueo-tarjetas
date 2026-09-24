import { defineConfig, devices } from "@playwright/test";

// Pruebas responsive con el backend simulado dentro del navegador (page.route): no requieren
// FastAPI ni gastan cuota de Groq. Todas corren en Chromium (el navegador objetivo es Chrome);
// "iPhone 14" emula viewport, DPR y touch de iPhone, no el motor WebKit.
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.results",
  fullyParallel: true,
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "pixel-7", use: { ...devices["Pixel 7"] } },
    { name: "iphone-14", use: { ...devices["iPhone 14"], defaultBrowserType: "chromium" } },
    { name: "android-360", use: { ...devices["Galaxy S9+"], viewport: { width: 360, height: 640 } } },
    { name: "narrow-320", use: { ...devices["Pixel 7"], viewport: { width: 320, height: 568 } } },
    { name: "tablet-768", use: { viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: "desktop-1440", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: {
    command: `npm run build && npx next start -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
  },
});
