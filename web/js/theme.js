/* ═══════════════════════════════════════════
   THEME TOGGLE
   Handles switching between purple glassmorphic theme and white theme
   ═══════════════════════════════════════════ */

(function() {
    'use strict';

    const THEME_KEY = 'btc-monitor-theme';
    const THEME_WHITE = 'theme-white';
    const THEME_PURPLE = 'theme-purple';

    // Initialize theme on page load
    function initTheme() {
        const savedTheme = localStorage.getItem(THEME_KEY);
        const themeToggle = document.getElementById('theme-toggle');
        
        if (!themeToggle) {
            console.warn('Theme toggle button not found');
            return;
        }

        // Apply saved theme or default to purple
        if (savedTheme === THEME_WHITE) {
            applyWhiteTheme();
        } else {
            applyPurpleTheme();
        }

        // Add click event listener
        themeToggle.addEventListener('click', toggleTheme);
    }

    // Toggle between themes
    function toggleTheme() {
        const body = document.body;
        const currentTheme = body.classList.contains(THEME_WHITE) ? THEME_WHITE : THEME_PURPLE;
        
        if (currentTheme === THEME_WHITE) {
            applyPurpleTheme();
        } else {
            applyWhiteTheme();
        }
    }

    // Apply white theme
    function applyWhiteTheme() {
        const body = document.body;
        const themeIcon = document.querySelector('.theme-icon');
        
        body.classList.remove(THEME_PURPLE);
        body.classList.add(THEME_WHITE);
        localStorage.setItem(THEME_KEY, THEME_WHITE);
        
        if (themeIcon) {
            themeIcon.innerHTML = '&#127769;'; // Moon icon for white theme
        }
    }

    // Apply purple theme
    function applyPurpleTheme() {
        const body = document.body;
        const themeIcon = document.querySelector('.theme-icon');
        
        body.classList.remove(THEME_WHITE);
        body.classList.add(THEME_PURPLE);
        localStorage.setItem(THEME_KEY, THEME_PURPLE);
        
        if (themeIcon) {
            themeIcon.innerHTML = '&#9728;'; // Sun icon for purple theme
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTheme);
    } else {
        initTheme();
    }

    // Export for potential external use
    window.ThemeManager = {
        toggleTheme,
        applyWhiteTheme,
        applyPurpleTheme,
        getCurrentTheme: () => {
            return document.body.classList.contains(THEME_WHITE) ? THEME_WHITE : THEME_PURPLE;
        }
    };
})();
