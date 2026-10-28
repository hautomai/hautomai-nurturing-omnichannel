/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        panel: "var(--panel)",
        "panel-2": "var(--panel-2)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        line: "var(--line)",
        accent: "var(--accent)",
        "accent-2": "var(--accent-2)",
        warm: "var(--warm)",
        danger: "var(--danger)",
        meta: "var(--meta)",
        "meta-2": "var(--meta-2)",
        "body-2": "var(--body-2)",
      },
      fontFamily: {
        display: ["Syne", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        panel: "28px",
        card: "18px",
        input: "9px",
        button: "11px",
        pill: "999px",
      },
      boxShadow: {
        panel: "0 30px 80px rgba(0,0,0,.38)",
        glow: "0 0 16px rgba(201,255,74,.55)",
      },
      letterSpacing: {
        eyebrow: "0.14em",
        kicker: "0.12em",
        display: "-0.05em",
        title: "-0.04em",
        card: "-0.03em",
      },
      transitionTimingFunction: {
        rise: "cubic-bezier(.2,.7,.2,1)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(24px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        scan: {
          "0%": { transform: "translateX(-160px)" },
          "100%": { transform: "translateX(60vw)" },
        },
        pulseglow: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: ".45" },
        },
      },
      animation: {
        rise: "rise .8s cubic-bezier(.2,.7,.2,1) both",
        scan: "scan 6s linear infinite",
        pulseglow: "pulseglow 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
