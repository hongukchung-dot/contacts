// 매체 팝업 · 전화번호 표시 토글 · 복사
(function () {
  "use strict";

  const backdrop = document.getElementById("modal-backdrop");
  const modalBody = document.getElementById("modal-body");

  function openModal(url) {
    if (!backdrop || !modalBody) return;
    modalBody.innerHTML = '<div class="modal"><div class="body">불러오는 중…</div></div>';
    backdrop.classList.add("open");
    document.body.style.overflow = "hidden";

    fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("불러오지 못했습니다 (" + response.status + ")");
        return response.text();
      })
      .then(function (html) {
        modalBody.innerHTML = html;
      })
      .catch(function (error) {
        modalBody.innerHTML =
          '<div class="modal"><div class="body"><div class="notice error">' +
          error.message +
          "</div></div></div>";
      });
  }

  function closeModal() {
    if (!backdrop) return;
    backdrop.classList.remove("open");
    document.body.style.overflow = "";
  }

  // navigator.clipboard 는 HTTPS·localhost 에서만 존재한다.
  // Tailscale IP 등 일반 HTTP 접속에서도 복사가 되도록 구식 방법으로 폴백한다.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "-1000px";
      document.body.appendChild(area);
      area.select();
      try {
        if (document.execCommand("copy")) resolve();
        else reject(new Error("브라우저가 복사를 허용하지 않았습니다"));
      } catch (error) {
        reject(error);
      } finally {
        document.body.removeChild(area);
      }
    });
  }

  document.addEventListener("click", function (event) {
    const opener = event.target.closest("[data-outlet]");
    if (opener) {
      event.preventDefault();
      openModal("/outlet/" + encodeURIComponent(opener.getAttribute("data-outlet")));
      return;
    }

    const modalLink = event.target.closest("[data-modal]");
    if (modalLink) {
      event.preventDefault();
      openModal(modalLink.getAttribute("data-modal"));
      return;
    }

    if (event.target.closest("[data-close]") || event.target === backdrop) {
      closeModal();
      return;
    }

    // 전화번호: 첫 클릭은 표시, 두 번째 클릭은 복사
    const phone = event.target.closest(".phone");
    if (phone) {
      const real = phone.getAttribute("data-phone");
      if (!real) return;
      if (phone.dataset.revealed !== "1") {
        phone.textContent = real;
        phone.dataset.revealed = "1";
        phone.title = "한 번 더 누르면 복사됩니다";
      } else {
        copyText(real).then(function () {
          const original = phone.textContent;
          phone.textContent = "복사됨";
          setTimeout(function () {
            phone.textContent = original;
          }, 800);
        });
      }
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
  });

  // 수정·삭제 폼: 페이지를 떠나지 않고 처리하고, 성공하면 화면을 새로 고친다.
  document.addEventListener("submit", function (event) {
    const form = event.target.closest("[data-edit-form]");
    if (!form) return;
    event.preventDefault();

    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
    })
      .then(function (response) {
        if (response.headers.get("X-Contacts-Edited")) {
          window.location.reload();
          return null;
        }
        return response.text();
      })
      .then(function (html) {
        if (html === null) return;
        modalBody.innerHTML = html;   // 입력 오류 → 폼을 다시 그린다
      })
      .catch(function (error) {
        if (submit) submit.disabled = false;
        alert("처리하지 못했습니다: " + error.message);
      });
  });

  // 팝업 안 '명단 전체 복사'
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-copy-list]");
    if (!button) return;
    const scope = document.getElementById(button.getAttribute("data-copy-list"));
    if (!scope) return;
    const lines = [];
    scope.querySelectorAll("[data-row]").forEach(function (row) {
      lines.push(row.getAttribute("data-row"));
    });
    const label = button.textContent;
    copyText(lines.join("\n"))
      .then(function () {
        button.textContent = "복사됨 (" + lines.length + "명)";
      })
      .catch(function () {
        button.textContent = "복사 실패 — 브라우저 설정을 확인하세요";
      })
      .then(function () {
        setTimeout(function () {
          button.textContent = label;
        }, 1200);
      });
  });

  // 검토 화면: 유형별 전체 선택/해제
  document.addEventListener("change", function (event) {
    const toggle = event.target.closest("[data-toggle-group]");
    if (!toggle) return;
    const group = document.getElementById(toggle.getAttribute("data-toggle-group"));
    if (!group) return;
    group.querySelectorAll('input[type="checkbox"]').forEach(function (box) {
      box.checked = toggle.checked;
    });
  });
})();
