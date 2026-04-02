/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0f172a",
        panel: "#111827",
        "panel-soft": "#1f2937",
        muted: "#9ca3af",
        ok: "#16a34a",
        warn: "#f59e0b",
        bad: "#ef4444",
        accent: "#38bdf8",
        border: "#334155",
      },
      fontFamily: {
        mono: [
          "SFMono-Regular",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
