"""Static scripts run in Chromium's isolated main-frame world.

Credentials are passed as typed arguments, never interpolated into source code.
The document token prevents a pending native selection filling a new document.
"""

WORLD = "mini-browser-passwords"

INITIALIZE = """
if (globalThis.isSecureContext && globalThis.crypto && crypto.randomUUID)
    globalThis.__miniBrowserDocument = crypto.randomUUID();
"""

PROBE = """
if (!globalThis.__miniBrowserDocument)
    globalThis.__miniBrowserDocument = crypto.randomUUID();
return JSON.stringify({origin: location.origin, token: globalThis.__miniBrowserDocument});
"""

FILL = """
if (window !== window.top || location.origin !== expectedOrigin ||
    globalThis.__miniBrowserDocument !== expectedDocument) return 'changed';
const visible = input => {
    const rect = input.getBoundingClientRect();
    return !input.disabled && !input.readOnly && rect.width >= 24 && rect.height >= 12 &&
        rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth &&
        getComputedStyle(input).visibility === 'visible';
};
let passwords = [...document.querySelectorAll('input[type="password"]')]
    .filter(input => visible(input) && input.autocomplete !== 'new-password');
const focused = document.activeElement;
if (focused && focused.form) passwords = passwords.filter(input => input.form === focused.form);
if (passwords.length !== 1) return 'form';
const passwordInput = passwords[0];
const form = passwordInput.form;
if (form) {
    const actionOrigin = new URL(form.action || location.href, location.href).origin;
    if (actionOrigin !== expectedOrigin) return 'action';
    // Avoid filling registration, password-change and confirmation forms.
    if (form.querySelectorAll('input[type="password"]').length !== 1) return 'form';
}
const scope = form || document;
const candidates = [...scope.querySelectorAll('input')].filter(input => visible(input) &&
    ['text', 'email', 'tel'].includes(input.type));
const usernameInput = candidates.find(input => input.autocomplete === 'username') ||
    candidates.find(input => input.type === 'email') ||
    candidates.find(input => /user|login|email/i.test(input.name || input.id)) ||
    (candidates.length === 1 ? candidates[0] : null);
const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
if (usernameInput) set.call(usernameInput, username);
set.call(passwordInput, password);
for (const input of [usernameInput, passwordInput].filter(Boolean)) {
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
}
return 'filled';
"""
