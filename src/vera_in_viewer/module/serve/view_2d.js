// 2D mode interaction for VeraInViewer.
//
// VTK.wasm cannot mirror a custom button mapping (vtkInteractorStyleManipulator
// has no serializer), so while the "view_2d" trame state is on, left button
// events targeting the 3D view are re-dispatched as middle button events. With
// the trackball camera style this gives: left/middle = pan, right/wheel = zoom.
//
// VTK.wasm listens to mousedown/mousemove on the canvas and mouseup on window,
// so window capture listeners see every event first.
(function () {
  const VIEW_SELECTOR = ".vera-view";
  const LEFT = 0;
  const MIDDLE = 1;
  let remapping = false; // a remapped drag is in progress

  function is2D() {
    return !!(window.trame && window.trame.state && window.trame.state.get("view_2d"));
  }

  function redispatch(event, target, button, buttons) {
    event.stopImmediatePropagation();
    event.preventDefault();
    const clone = new MouseEvent(event.type, {
      bubbles: true,
      cancelable: true,
      view: window,
      detail: event.detail,
      screenX: event.screenX,
      screenY: event.screenY,
      clientX: event.clientX,
      clientY: event.clientY,
      ctrlKey: event.ctrlKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      metaKey: event.metaKey,
      button,
      buttons,
    });
    clone.__veraRemapped = true;
    target.dispatchEvent(clone);
  }

  function inView(event) {
    return event.target instanceof Element && event.target.closest(VIEW_SELECTOR);
  }

  window.addEventListener(
    "mousedown",
    (event) => {
      if (event.__veraRemapped || event.button !== LEFT || !is2D() || !inView(event)) {
        return;
      }
      remapping = true;
      redispatch(event, event.target, MIDDLE, 4);
    },
    true,
  );

  window.addEventListener(
    "mousemove",
    (event) => {
      if (event.__veraRemapped || !remapping || !inView(event)) {
        return;
      }
      redispatch(event, event.target, event.button, (event.buttons & ~1) | 4);
    },
    true,
  );

  window.addEventListener(
    "mouseup",
    (event) => {
      if (event.__veraRemapped || !remapping || event.button !== LEFT) {
        return;
      }
      remapping = false;
      redispatch(event, event.target, MIDDLE, event.buttons & ~1);
    },
    true,
  );
})();
