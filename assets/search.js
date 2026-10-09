// Client-side full-text search over data/search.json (built by scripts/publish_post.py).
// Each record is one section of one day's brief: {date, display_date, title, url, section, text}.
(function () {
  var input = document.getElementById("search-input");
  var results = document.getElementById("search-results");
  var clearBtn = document.getElementById("search-clear");
  var archive = document.querySelector("[data-hide-on-search]");
  var archiveHeading = null;
  if (!input || !results) return;

  var index = null;
  var loading = null;

  function load() {
    if (!loading) {
      loading = fetch("data/search.json", { cache: "no-cache" })
        .then(function (r) { return r.json(); })
        .then(function (data) { index = data; return data; })
        .catch(function () { index = []; return index; });
    }
    return loading;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function terms(q) {
    return q.toLowerCase().split(/\s+/).filter(function (t) { return t.length > 1; });
  }

  function snippet(text, ts) {
    var lower = text.toLowerCase();
    var pos = -1;
    for (var i = 0; i < ts.length; i++) {
      var p = lower.indexOf(ts[i]);
      if (p !== -1 && (pos === -1 || p < pos)) pos = p;
    }
    var start = Math.max(0, pos - 90);
    var end = Math.min(text.length, (pos === -1 ? 0 : pos) + 220);
    var out = text.slice(start, end);
    if (start > 0) out = "…" + out;
    if (end < text.length) out = out + "…";
    var html = escapeHtml(out);
    ts.forEach(function (t) {
      html = html.replace(new RegExp("(" + escapeRe(escapeHtml(t)) + ")", "gi"), "<mark>$1</mark>");
    });
    return html;
  }

  function score(rec, ts) {
    var hay = (rec.title + " " + rec.section + " " + rec.text).toLowerCase();
    var s = 0;
    for (var i = 0; i < ts.length; i++) {
      if (hay.indexOf(ts[i]) === -1) return 0; // every term must match
      var count = hay.split(ts[i]).length - 1;
      s += count;
      if (rec.section.toLowerCase().indexOf(ts[i]) !== -1) s += 3;
    }
    return s;
  }

  function render(q) {
    var ts = terms(q);
    if (!ts.length) {
      results.hidden = true;
      results.innerHTML = "";
      if (archive) archive.hidden = false;
      if (archiveHeading) archiveHeading.hidden = false;
      clearBtn.hidden = true;
      return;
    }
    clearBtn.hidden = false;
    load().then(function (data) {
      var hits = data
        .map(function (rec) { return { rec: rec, s: score(rec, ts) }; })
        .filter(function (h) { return h.s > 0; })
        .sort(function (a, b) { return b.rec.date.localeCompare(a.rec.date) || b.s - a.s; });

      var days = {};
      hits.forEach(function (h) { days[h.rec.date] = true; });
      var nDays = Object.keys(days).length;

      var html = '<p class="search-summary">' +
        (hits.length
          ? hits.length + " match" + (hits.length === 1 ? "" : "es") + " across " + nDays + " brief" + (nDays === 1 ? "" : "s")
          : "No matches for “" + escapeHtml(q) + "”") +
        "</p>";

      var lastDate = null;
      hits.slice(0, 100).forEach(function (h) {
        var r = h.rec;
        if (r.date !== lastDate) {
          html += '<h3 class="search-day"><a href="' + escapeHtml(r.url.split("#")[0]) + '">' + escapeHtml(r.display_date) + "</a></h3>";
          lastDate = r.date;
        }
        html += '<a class="search-hit" href="' + escapeHtml(r.url) + '">' +
          '<span class="search-section">' + escapeHtml(r.section || r.title) + "</span>" +
          '<span class="search-snippet">' + snippet(r.text, ts) + "</span>" +
          "</a>";
      });
      results.innerHTML = html;
      results.hidden = false;
      if (archive) archive.hidden = true;
      if (archiveHeading) archiveHeading.hidden = true;
    });
  }

  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { render(input.value); }, 120);
  });
  input.addEventListener("focus", load);
  clearBtn.addEventListener("click", function () {
    input.value = "";
    render("");
    input.focus();
  });

  // Support ?q=... deep links and the #search nav link.
  var params = new URLSearchParams(location.search);
  if (params.get("q")) {
    input.value = params.get("q");
    render(input.value);
  }
  if (location.hash === "#search") input.focus();
})();
