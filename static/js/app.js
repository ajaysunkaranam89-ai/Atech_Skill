/* A TechSkills Academy – shared front-end behaviour (no build step required). */
(function () {
  "use strict";

  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }
  window.tsCsrf = function () {
    const el = document.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : getCookie("csrftoken");
  };

  // ---------------------------------------------------------------- confirmation dialogs
  // <form data-confirm="Are you sure?"> or <button data-confirm="...">
  document.addEventListener("submit", function (e) {
    const form = e.target;
    const msg = form.getAttribute("data-confirm") || (e.submitter && e.submitter.getAttribute("data-confirm"));
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  // ---------------------------------------------------------------- typed delete confirmation
  document.querySelectorAll("[data-typed-confirm]").forEach(function (input) {
    const btn = document.getElementById(input.getAttribute("data-typed-confirm"));
    const expected = input.getAttribute("data-expected").trim();
    const update = function () { btn.disabled = input.value.trim() !== expected; };
    input.addEventListener("input", update);
    update();
  });

  // ---------------------------------------------------------------- sidebar toggle (admin)
  document.querySelectorAll("[data-toggle-sidebar]").forEach(function (b) {
    b.addEventListener("click", function () { document.querySelector(".dash-sidebar").classList.toggle("show"); });
  });

  // ---------------------------------------------------------------- module select filtered by course
  document.querySelectorAll("select[data-filter-by-course]").forEach(function (moduleSel) {
    const form = moduleSel.closest("form");
    const courseSel = form.querySelector("select[name=course]");
    if (!courseSel) return;
    const all = Array.from(moduleSel.options).map(function (o) { return { value: o.value, text: o.text, selected: o.selected }; });
    const courseNames = {};
    Array.from(courseSel.options).forEach(function (o) { courseNames[o.value] = o.text; });
    function apply() {
      const cname = courseNames[courseSel.value];
      const current = moduleSel.value;
      moduleSel.innerHTML = "";
      all.forEach(function (o) {
        if (!o.value || !cname || o.text.indexOf(cname + " › ") === 0) {
          const opt = new Option(cname && o.value ? o.text.substring(cname.length + 3) : o.text, o.value);
          if (o.value === current) opt.selected = true;
          moduleSel.add(opt);
        }
      });
    }
    courseSel.addEventListener("change", apply);
    apply();
  });

  // ---------------------------------------------------------------- video source field toggling
  document.querySelectorAll("select[name=source_type]").forEach(function (sel) {
    const form = sel.closest("form");
    const urlRow = form.querySelector("[data-field=video_url]");
    const fileRow = form.querySelector("[data-field=video_file]");
    const hint = form.querySelector("#source-hint");
    const hints = {
      youtube: "Paste the YouTube link (unlisted recommended), e.g. https://youtu.be/XXXXXXXXXXX",
      youtube_embed: "Paste an embed URL, e.g. https://www.youtube.com/embed/XXXXXXXXXXX",
      external: "HTTPS link to an MP4/HLS file on a video host or CDN.",
      upload: "Upload an MP4 (H.264/AAC). Stored privately and streamed only to authorised students.",
      "": "This lesson will have no video (text / PDF lesson). You can add a video later."
    };
    function apply() {
      const v = sel.value;
      if (urlRow) urlRow.style.display = (v === "upload" || v === "") ? "none" : "";
      if (fileRow) fileRow.style.display = v === "upload" ? "" : "none";
      if (hint) hint.textContent = hints[v] || "";
    }
    sel.addEventListener("change", apply);
    apply();
  });

  // ---------------------------------------------------------------- notify batches visibility
  document.querySelectorAll("select[name=notify]").forEach(function (sel) {
    const row = sel.closest("form").querySelector("[data-field=notify_batches]");
    function apply() { if (row) row.style.display = sel.value === "batches" ? "" : "none"; }
    sel.addEventListener("change", apply);
    apply();
  });

  // ---------------------------------------------------------------- upload progress
  // Forms with data-upload-progress are sent via XHR when they contain a file, showing a progress bar.
  document.querySelectorAll("form[data-upload-progress]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      const hasFile = Array.from(form.querySelectorAll("input[type=file]")).some(function (i) { return i.files && i.files.length; });
      if (!hasFile || !window.XMLHttpRequest || !window.FormData) return;
      e.preventDefault();
      const box = form.querySelector(".upload-progress");
      const bar = box ? box.querySelector(".progress-bar") : null;
      const label = box ? box.querySelector(".upload-label") : null;
      const buttons = form.querySelectorAll("button[type=submit]");
      buttons.forEach(function (b) { b.disabled = true; });
      if (box) box.classList.add("active");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || window.location.href);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.upload.addEventListener("progress", function (ev) {
        if (!ev.lengthComputable || !bar) return;
        const pct = Math.round((ev.loaded / ev.total) * 100);
        bar.style.width = pct + "%";
        bar.textContent = pct + "%";
        if (label) label.textContent = pct < 100 ? "Uploading… " + (ev.loaded / 1048576).toFixed(1) + " / " + (ev.total / 1048576).toFixed(1) + " MB" : "Processing on server…";
      });
      xhr.onload = function () {
        const ct = xhr.getResponseHeader("Content-Type") || "";
        if (ct.indexOf("application/json") !== -1) {
          try {
            const data = JSON.parse(xhr.responseText);
            if (data.redirect) { window.location.href = data.redirect; return; }
          } catch (err) { /* fall through */ }
        }
        // Validation errors: server returned the form HTML; show it.
        document.open(); document.write(xhr.responseText); document.close();
      };
      xhr.onerror = function () {
        buttons.forEach(function (b) { b.disabled = false; });
        if (label) label.textContent = "Upload failed – check your connection and try again.";
      };
      xhr.send(new FormData(form));
    });
  });

  // ---------------------------------------------------------------- mark lesson complete (AJAX)
  document.querySelectorAll("form[data-complete-form]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const fd = new FormData(form);
      fetch(form.action, { method: "POST", body: fd, headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          const btn = form.querySelector("button");
          const input = form.querySelector("input[name=completed]");
          if (data.completed) {
            btn.className = "btn btn-success"; btn.innerHTML = '<i class="bi bi-check-circle-fill me-1"></i>Completed';
            input.value = "0";
          } else {
            btn.className = "btn btn-outline-success"; btn.innerHTML = '<i class="bi bi-check-circle me-1"></i>Mark as completed';
            input.value = "1";
          }
          document.querySelectorAll("[data-course-progress]").forEach(function (el) {
            el.style.width = data.progress.percent + "%";
          });
          document.querySelectorAll("[data-course-progress-label]").forEach(function (el) {
            el.textContent = data.progress.percent + "% · " + data.progress.completed + "/" + data.progress.total + " lessons";
          });
          if (data.certificate_url) {
            const box = document.createElement("div");
            box.className = "alert alert-success d-flex align-items-center gap-2 mt-3";
            box.innerHTML = '<i class="bi bi-award fs-4"></i><div class="me-auto"><strong>Congratulations!</strong> You completed the course and earned a certificate.</div>';
            const a = document.createElement("a");
            a.className = "btn btn-sm btn-success"; a.href = data.certificate_url; a.textContent = "View certificate";
            box.appendChild(a);
            form.closest(".d-flex").after(box);
          }
          const icon = document.querySelector("[data-lesson-icon='" + form.dataset.lesson + "']");
          if (icon) icon.className = data.completed ? "bi bi-check-circle-fill text-success" : "bi bi-play-circle text-muted";
        })
        .catch(function () { form.submit(); });
    });
  });
})();
