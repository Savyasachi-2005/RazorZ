/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#070b14",
        surface: {
          DEFAULT: "#0d1424",
          raised: "#121a2c",
          hover: "#182236",
        },
        line: {
          DEFAULT: "#1e2a3f",
          strong: "#2a3a55",
        },
        ink: {
          DEFAULT: "#f1f5f9",
          muted: "#94a3b8",
          faint: "#64748b",
        },
        accent: {
          DEFAULT: "#16a34a",
          soft: "#14532d",
          text: "#4ade80",
        },
        warn: {
          DEFAULT: "#d97706",
          soft: "#78350f",
          text: "#fbbf24",
        },
        danger: {
          DEFAULT: "#e11d48",
          soft: "#881337",
          text: "#fb7185",
        },
        info: {
          DEFAULT: "#2563eb",
          soft: "#1e3a5f",
          text: "#93c5fd",
        },
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.25)",
      },
      maxWidth: {
        shell: "1480px",
      },
      transitionDuration: {
        fast: "150ms",
      },
    },
  },
  plugins: [],
};
