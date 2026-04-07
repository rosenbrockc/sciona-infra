/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Core surfaces — deep navy, inspired by badge interior brushed metal
        bg: "#06091a",
        "bg-soft": "#0b1026",
        panel: "#0d1229",
        "panel-soft": "#141a36",
        "panel-bright": "#1a2244",

        // Text
        muted: "#7b8ab5",

        // Status
        ok: "#22c55e",
        warn: "#f59e0b",
        bad: "#ef4444",

        // Primary accent — the cyan glow from Edge-tier badges
        accent: "#38bdf8",
        "accent-bright": "#7dd3fc",

        // Secondary accent — the indigo/purple from evangelist puck
        "accent-2": "#818cf8",

        // Track colors — from the physical hex pucks
        "track-originator": "#d4875c",   // copper
        "track-architect": "#5b8dd9",    // steel blue
        "track-vanguard": "#34d399",     // emerald
        "track-evangelist": "#a78bfa",   // purple

        // Tier colors — from the badge frames
        "tier-node": "#c87a50",       // bronze/copper
        "tier-edge": "#94a3b8",       // silver
        "tier-lattice": "#d4a843",    // gold

        // Borders
        border: "#1a2244",
        "border-bright": "#253262",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 0 20px rgba(56, 189, 248, 0.12), 0 0 4px rgba(56, 189, 248, 0.15)",
        "glow-lg": "0 0 40px rgba(56, 189, 248, 0.18), 0 0 8px rgba(56, 189, 248, 0.2)",
        "glow-gold": "0 0 20px rgba(212, 168, 67, 0.15), 0 0 4px rgba(212, 168, 67, 0.2)",
        card: "0 2px 8px rgba(0, 0, 0, 0.4), 0 0 1px rgba(56, 189, 248, 0.05)",
        "card-hover": "0 8px 24px rgba(0, 0, 0, 0.5), 0 0 1px rgba(56, 189, 248, 0.1)",
        "inner-glow": "inset 0 1px 0 rgba(56, 189, 248, 0.06)",
      },
      backgroundImage: {
        "accent-gradient": "linear-gradient(135deg, #38bdf8 0%, #818cf8 100%)",
        "gold-gradient": "linear-gradient(135deg, #c87a50 0%, #d4a843 50%, #c87a50 100%)",
        "card-gradient": "linear-gradient(180deg, rgba(20, 26, 54, 0.8) 0%, rgba(13, 18, 41, 0.9) 100%)",
        "card-gradient-hover": "linear-gradient(180deg, rgba(26, 34, 68, 0.9) 0%, rgba(13, 18, 41, 0.9) 100%)",
        "hero-gradient": "radial-gradient(ellipse at 30% 50%, rgba(56, 189, 248, 0.08) 0%, transparent 60%), radial-gradient(ellipse at 70% 50%, rgba(129, 140, 248, 0.06) 0%, transparent 60%)",
        "sidebar-gradient": "linear-gradient(180deg, #0b1026 0%, #06091a 100%)",
      },
      animation: {
        "fade-in": "fadeIn 0.4s ease-out",
        "slide-up": "slideUp 0.4s ease-out",
        "pulse-slow": "pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "glow-pulse": "glowPulse 3s ease-in-out infinite",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        glowPulse: {
          "0%, 100%": { opacity: "0.4" },
          "50%": { opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};
