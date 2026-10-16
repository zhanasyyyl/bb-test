// Reports the device's real battery percentage where the browser exposes it
// (Battery Status API: Chrome, Edge and other Chromium browsers; not Safari or
// Firefox). Calls onLevel(percent) right away and again whenever it changes.
// Returns a promise that resolves to false if the real level isn't available,
// so the page can keep its own placeholder. Devices without a battery
// (desktops) report 100%.
function watchBattery(onLevel) {
    if (!navigator.getBattery) {
        return Promise.resolve(false);
    }
    return navigator.getBattery().then(function (battery) {
        var report = function () {
            onLevel(Math.round(battery.level * 100));
        };
        report();
        battery.addEventListener('levelchange', report);
        return true;
    }).catch(function () {
        // e.g. blocked by a Permissions-Policy or an insecure (HTTP) page
        return false;
    });
}
