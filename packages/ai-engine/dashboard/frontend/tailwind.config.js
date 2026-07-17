/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#0a0a1a',
          raised: '#12122a',
          overlay: '#1a1a3a',
        },
        accent: {
          DEFAULT: '#4a9eff',
          hover: '#6bb3ff',
          muted: '#2a6cbf',
        },
        profit: '#00ff88',
        loss: '#ff4444',
        warning: '#f59e0b',
        info: '#3b82f6',
        neutral: {
          100: '#f0f0f0',
          200: '#d0d0d0',
          300: '#a0a0a0',
          400: '#707070',
          500: '#505050',
          600: '#3a3a3a',
          700: '#2a2a2a',
          800: '#1a1a1a',
          900: '#0a0a0a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'slide-up': 'slideUp 0.3s ease-out',
      },
      keyframes: {
        slideUp: {
          '0%': { transform: 'translateY(10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
