/*
 * XaninFarm storefront behaviour.
 *
 * Deliberately small and dependency-free: htmx does the network work, this file
 * only handles the things htmx cannot (menus, dropdowns, toasts, galleries).
 * Everything uses event delegation on `document`, so markup swapped in by htmx
 * is wired up automatically with no re-initialisation step.
 */
(function () {
  'use strict';

  var TOAST_TIMEOUT = 6000;

  // ---------------------------------------------------------------------
  // Mobile navigation
  // ---------------------------------------------------------------------
  function toggleMobileNav(button) {
    var panel = document.getElementById(button.getAttribute('aria-controls'));
    if (!panel) return;
    var open = panel.hasAttribute('hidden');
    if (open) {
      panel.removeAttribute('hidden');
    } else {
      panel.setAttribute('hidden', '');
    }
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  // ---------------------------------------------------------------------
  // Dropdowns
  // ---------------------------------------------------------------------
  function closeDropdowns(except) {
    document.querySelectorAll('[data-dropdown]').forEach(function (wrapper) {
      if (wrapper === except) return;
      var panel = wrapper.querySelector('[data-dropdown-panel]');
      var toggle = wrapper.querySelector('[data-dropdown-toggle]');
      if (panel) panel.setAttribute('hidden', '');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleDropdown(toggle) {
    var wrapper = toggle.closest('[data-dropdown]');
    if (!wrapper) return;
    var panel = wrapper.querySelector('[data-dropdown-panel]');
    if (!panel) return;

    var open = panel.hasAttribute('hidden');
    closeDropdowns(wrapper);
    if (open) {
      panel.removeAttribute('hidden');
    } else {
      panel.setAttribute('hidden', '');
    }
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  // ---------------------------------------------------------------------
  // Toasts
  // ---------------------------------------------------------------------
  function dismissToast(toast) {
    if (!toast || toast.dataset.dismissing) return;
    toast.dataset.dismissing = '1';
    toast.style.transition = 'opacity 180ms ease, transform 180ms ease';
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-6px)';
    window.setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 200);
  }

  function scheduleToasts() {
    document.querySelectorAll('[data-toast]').forEach(function (toast) {
      if (toast.dataset.scheduled) return;
      toast.dataset.scheduled = '1';
      window.setTimeout(function () {
        dismissToast(toast);
      }, TOAST_TIMEOUT);
    });
  }

  // ---------------------------------------------------------------------
  // Cart badge
  //
  // The cart panel renders the authoritative quantity into a hidden element.
  // We never compute it here: the server owns the number.
  // ---------------------------------------------------------------------
  function syncCartCount() {
    var source = document.querySelector('[data-cart-total-quantity]');
    if (!source) return;
    var count = parseInt(source.getAttribute('data-cart-total-quantity'), 10) || 0;

    document.querySelectorAll('[data-cart-count]').forEach(function (badge) {
      badge.textContent = String(count);
      badge.classList.toggle('hidden', count === 0);
    });
  }

  // ---------------------------------------------------------------------
  // Product gallery
  // ---------------------------------------------------------------------
  function selectGalleryImage(thumb) {
    var main = document.querySelector('[data-gallery-main]');
    if (!main) return;
    main.setAttribute('src', thumb.getAttribute('data-full') || thumb.getAttribute('src'));
    main.setAttribute('alt', thumb.getAttribute('alt') || '');
    document.querySelectorAll('[data-gallery-thumb]').forEach(function (other) {
      other.setAttribute('aria-current', other === thumb ? 'true' : 'false');
    });
  }

  // ---------------------------------------------------------------------
  // Quantity steppers
  // ---------------------------------------------------------------------
  function stepQuantity(button) {
    var wrapper = button.closest('[data-qty]');
    if (!wrapper) return;
    var input = wrapper.querySelector('input[type="number"]');
    if (!input) return;

    var step = parseInt(button.getAttribute('data-qty-step'), 10) || 1;
    var min = parseInt(input.getAttribute('min'), 10);
    var max = parseInt(input.getAttribute('max'), 10);
    var next = (parseInt(input.value, 10) || 0) + step;

    if (!isNaN(min)) next = Math.max(min, next);
    if (!isNaN(max)) next = Math.min(max, next);
    if (next === (parseInt(input.value, 10) || 0)) return;

    input.value = String(next);
    // Let htmx-bound inputs and native listeners react to the new value.
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ---------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------
  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;

    var menuToggle = target.closest('[data-menu-toggle]');
    if (menuToggle) {
      toggleMobileNav(menuToggle);
      return;
    }

    var dropdownToggle = target.closest('[data-dropdown-toggle]');
    if (dropdownToggle) {
      event.preventDefault();
      toggleDropdown(dropdownToggle);
      return;
    }

    var dismiss = target.closest('[data-toast-dismiss]');
    if (dismiss) {
      dismissToast(dismiss.closest('[data-toast]'));
      return;
    }

    var thumb = target.closest('[data-gallery-thumb]');
    if (thumb) {
      event.preventDefault();
      selectGalleryImage(thumb);
      return;
    }

    var step = target.closest('[data-qty-step]');
    if (step) {
      event.preventDefault();
      stepQuantity(step);
      return;
    }

    // A click anywhere else closes any open dropdown.
    if (!target.closest('[data-dropdown]')) closeDropdowns(null);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    closeDropdowns(null);
    document.querySelectorAll('[data-menu-toggle][aria-expanded="true"]').forEach(toggleMobileNav);
  });

  // Toasts arriving with the page, and with every htmx swap.
  document.addEventListener('DOMContentLoaded', function () {
    scheduleToasts();
    syncCartCount();
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    scheduleToasts();
    syncCartCount();
  });

  // Cart mutations announce themselves with HX-Trigger (see apps.core.mixins).
  document.body.addEventListener('cart:changed', syncCartCount);

  /*
   * The cart views answer a rejected mutation - out of stock, bad quantity -
   * with 422 and the re-rendered panel, which carries the explanation. htmx
   * refuses to swap error responses by default, so opt this one status in.
   */
  document.body.addEventListener('htmx:beforeSwap', function (event) {
    if (event.detail.xhr && event.detail.xhr.status === 422) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
})();
