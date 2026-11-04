// Reports the device's real battery percentage and charging state where the
// browser exposes it (Battery Status API: Chrome, Edge and other Chromium
// browsers; not Safari or Firefox). Calls onLevel(percent, charging) right
// away and again whenever either changes. Returns a promise that resolves to
// false if the real level isn't available, so the page can keep its own
// placeholder. Devices without a battery (desktops) report 100%, charging.
function watchBattery(onLevel) {
    if (!navigator.getBattery) {
        return Promise.resolve(false);
    }
    return navigator.getBattery().then(function (battery) {
        var report = function () {
            onLevel(Math.round(battery.level * 100), battery.charging);
        };
        report();
        battery.addEventListener('levelchange', report);
        battery.addEventListener('chargingchange', report);
        return true;
    }).catch(function () {
        // e.g. blocked by a Permissions-Policy or an insecure (HTTP) page
        return false;
    });
}

// The icon that best matches a percentage: icons exist for 0, 15, 25, 50, 75
// and 100%, and we round to the nearest one (so the red low-battery icon
// shows below 20%).
function batteryIconStep(percent) {
    if (percent >= 88) return 100;
    if (percent >= 63) return 75;
    if (percent >= 38) return 50;
    if (percent >= 20) return 25;
    if (percent >= 8) return 15;
    return 0;
}

// Key into window.BATTERY_ICONS (partials/_battery_icons.html). theme is
// "black" (light backgrounds) or "white" (dark backgrounds). The non-charging
// 0% and 15% icons are red and shared by both themes.
function batteryIconKey(percent, charging, theme) {
    var step = batteryIconStep(percent);
    if (charging) return theme + '-charge-' + step;
    if (step <= 15) return String(step);
    return theme + '-' + step;
}

// Updates every <img class="battery-icon" data-theme="black|white">.
function setBatteryIcons(percent, charging) {
    var icons = window.BATTERY_ICONS || {};
    document.querySelectorAll('img.battery-icon').forEach(function (img) {
        var src = icons[batteryIconKey(percent, charging, img.dataset.theme)];
        if (src && img.getAttribute('src') !== src) {
            img.src = src;
        }
    });
}
