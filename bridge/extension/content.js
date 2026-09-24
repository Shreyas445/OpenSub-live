/**
 * OpenSub Live Content Script
 * Injects mse_hook.js into the main page context to hook MediaSource and SourceBuffer.
 */
(function() {
    function injectScript(file_path, tag) {
        const node = document.getElementsByTagName(tag)[0] || document.documentElement;
        const script = document.createElement('script');
        script.setAttribute('type', 'text/javascript');
        script.setAttribute('src', chrome.runtime.getURL(file_path));
        node.appendChild(script);
    }

    injectScript('mse_hook.js', 'head');
})();
