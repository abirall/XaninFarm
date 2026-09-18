/** @type {import('tailwindcss').Config} */

// XaninFarm design system.
// A premium farm brand: deep forest greens, warm cream paper, honey gold.
// Deliberately not a generic grocery marketplace palette.
module.exports = {
  content: [
    './templates/**/*.html',
    './apps/**/templates/**/*.html',
    './apps/**/*.py',
    // The browser JS lives in static/js, not assets/js. A class that only ever
    // appears in a classList call is invisible to Tailwind without this, and
    // gets purged out of the production build.
    './static/js/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        // Primary brand green - pasture and leaf.
        farm: {
          50: '#F1F7F2',
          100: '#DDEBE0',
          200: '#BBD7C3',
          300: '#8FBB9E',
          400: '#5E9B74',
          500: '#3D7E56',
          600: '#2C6543',
          700: '#245036',
          800: '#1D402C',
          900: '#183524',
          950: '#0C1D14',
        },
        // Warm paper background - unbleached cream.
        cream: {
          50: '#FEFDFB',
          100: '#FAF7F0',
          200: '#F3EDE0',
          300: '#E9DFCB',
          400: '#DBCBAD',
          500: '#C9B48C',
        },
        // Accent - raw honey and ghee.
        honey: {
          50: '#FDF9ED',
          100: '#FAF0CE',
          200: '#F5E09D',
          300: '#EDCA64',
          400: '#E4B23A',
          500: '#C99A2E',
          600: '#A87B24',
          700: '#855D1F',
          800: '#6B4A1E',
          900: '#5A3E1D',
        },
        // Neutral ink.
        charcoal: {
          50: '#F6F6F5',
          100: '#E7E7E4',
          200: '#CFCEC9',
          300: '#AEACA4',
          400: '#8A877D',
          500: '#6E6B62',
          600: '#57544D',
          700: '#464440',
          800: '#2B2A26',
          900: '#1C1B18',
        },
      },
      fontFamily: {
        // Loaded in templates/base.html via Google Fonts with system fallbacks.
        display: ['Fraunces', 'Georgia', 'Cambria', 'serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
      fontSize: {
        'display-sm': ['2.125rem', { lineHeight: '1.12', letterSpacing: '-0.022em' }],
        'display-md': ['2.875rem', { lineHeight: '1.08', letterSpacing: '-0.028em' }],
        'display-lg': ['3.875rem', { lineHeight: '1.02', letterSpacing: '-0.034em' }],
        'display-xl': ['5rem', { lineHeight: '0.98', letterSpacing: '-0.038em' }],
        // Editorial hero scale. Optical tracking has to tighten as size grows or
        // large Fraunces settings look loose and amateurish.
        'display-2xl': ['6.5rem', { lineHeight: '0.94', letterSpacing: '-0.042em' }],
      },
      boxShadow: {
        // Layered rather than single-stop. Real depth comes from a tight contact
        // shadow plus a wide ambient one; a lone large blur reads as a grey
        // smudge under the card, which is what dates a UI fastest.
        soft: '0 1px 2px rgba(28,27,24,0.04), 0 2px 8px -2px rgba(28,27,24,0.05)',
        card: '0 1px 2px rgba(28,27,24,0.04), 0 4px 12px -2px rgba(28,27,24,0.05), 0 12px 32px -8px rgba(28,27,24,0.07)',
        'card-hover':
          '0 2px 4px rgba(28,27,24,0.05), 0 10px 24px -4px rgba(28,27,24,0.09), 0 28px 60px -12px rgba(28,27,24,0.14)',
        lift: '0 20px 50px -12px rgba(24,53,36,0.28)',
        inset: 'inset 0 1px 0 rgba(255,255,255,0.6)',
      },
      borderRadius: {
        '4xl': '2rem',
        '5xl': '2.5rem',
      },
      spacing: {
        18: '4.5rem',
        22: '5.5rem',
      },
      maxWidth: {
        '8xl': '88rem',
      },
      transitionTimingFunction: {
        'out-soft': 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.97)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-in-right': {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.4s ease-out both',
        'fade-up': 'fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both',
        'scale-in': 'scale-in 0.25s cubic-bezier(0.22, 1, 0.36, 1) both',
        'slide-in-right': 'slide-in-right 0.3s cubic-bezier(0.22, 1, 0.36, 1) both',
      },
    },
  },
  plugins: [require('@tailwindcss/forms'), require('@tailwindcss/typography')],
};
