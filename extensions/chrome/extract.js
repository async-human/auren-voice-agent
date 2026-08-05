(() => {
  const NOISE = new Set([
    "NAV",
    "FOOTER",
    "HEADER",
    "ASIDE",
    "SCRIPT",
    "STYLE",
    "NOSCRIPT",
    "SVG",
    "FORM",
    "BUTTON",
    "INPUT",
    "TEXTAREA",
    "SELECT",
    "IFRAME",
  ]);

  function visibleText(node) {
    if (!node || NOISE.has(node.nodeName)) {
      return "";
    }
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent || "";
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }
    const style = window.getComputedStyle(node);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {
      return "";
    }
    let out = "";
    for (const child of node.childNodes) {
      out += visibleText(child);
      if (child.nodeName === "P" || child.nodeName === "BR" || child.nodeName === "LI") {
        out += "\n";
      }
    }
    return out;
  }

  function scoreRoot(root) {
    const paragraphs = root.querySelectorAll("p");
    let score = 0;
    paragraphs.forEach((p) => {
      const len = (p.textContent || "").trim().length;
      if (len > 80) {
        score += len;
      }
    });
    return score;
  }

  function pickRoot() {
    const candidates = [
      document.querySelector("article"),
      document.querySelector("main"),
      document.querySelector("[role='main']"),
      document.querySelector(".post-content, .article-content, .entry-content, #content"),
      document.body,
    ].filter(Boolean);

    let best = candidates[0];
    let bestScore = -1;
    for (const candidate of candidates) {
      const score = scoreRoot(candidate);
      if (score > bestScore) {
        best = candidate;
        bestScore = score;
      }
    }
    return best || document.body;
  }

  function extract() {
    const root = pickRoot();
    const clone = root.cloneNode(true);
    clone
      .querySelectorAll(
        "nav, footer, header, aside, script, style, noscript, svg, form, button, iframe, [aria-hidden='true'], .ad, .ads, .advert, .sidebar, .comments"
      )
      .forEach((node) => node.remove());

    let text = visibleText(clone).replace(/\n{3,}/g, "\n\n").trim();
    if (text.length < 200) {
      text = visibleText(document.body).replace(/\n{3,}/g, "\n\n").trim();
    }

    const title =
      document.querySelector("h1")?.textContent?.trim() ||
      document.title?.trim() ||
      "Untitled page";

    return {
      url: location.href,
      title,
      text,
      charCount: text.length,
    };
  }

  return extract();
})();
