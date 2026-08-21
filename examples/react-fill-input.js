// Fill a React-controlled <input> with a value that React will see.
//
// Why this is needed: React wraps the DOM element's `value` setter to track changes
// via its internal event system. Setting `input.value = "..."` directly bypasses
// React's tracker — the DOM shows the new value but React state stays stale, and
// the onChange handler never fires — so a React-controlled form ignores a plain
// assignment no matter what the rendered input looks like afterwards.
//
// Fix: call the native HTMLInputElement.prototype value setter (the "real" one,
// before React swapped it), then dispatch an 'input' event so React's delegated
// listener picks it up.
//
// Template vars (python str.replace):
//   __SELECTOR__  CSS selector for the input (e.g. 'input[name="email"]' or
//                 'input[autocomplete="one-time-code"]')
//   __VALUE__     the value to fill (single-quote safe — escape backslashes and
//                 single quotes before substituting)

function setReactInput(el, value) {
  const proto = Object.getPrototypeOf(el);
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

const input = document.querySelector('__SELECTOR__');
if (!input) return 'NO_INPUT';

setReactInput(input, '__VALUE__');
return 'filled len=' + input.value.length;
