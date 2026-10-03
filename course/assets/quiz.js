/* Self-scoring multiple choice. Markup contract: references/quiz.md.
   A question is fieldset.q[data-answer=<value of the right radio>]. Choosing an
   option shows that option's .why text; the question locks once it is right.
   When every question is right, the .gate element is revealed. Nothing is stored
   or sent anywhere. */
(function () {
  function init(quiz) {
    var questions = Array.prototype.slice.call(quiz.querySelectorAll("fieldset.q"));
    var gate = quiz.querySelector(".gate");
    var score = quiz.querySelector(".score");

    function update() {
      var right = questions.filter(function (q) { return q.classList.contains("is-correct"); }).length;
      if (score) score.textContent = right + " of " + questions.length + " correct";
      if (gate) gate.hidden = right !== questions.length;
    }

    questions.forEach(function (q) {
      var feedback = q.querySelector(".feedback");
      q.addEventListener("change", function (e) {
        var input = e.target;
        if (!input || input.type !== "radio") return;
        if (q.classList.contains("is-correct")) {
          var right = q.querySelector('input[value="' + q.getAttribute("data-answer") + '"]');
          if (right) right.checked = true;
          return;
        }
        var label = input.closest("label");
        var isRight = input.value === q.getAttribute("data-answer");
        var why = label.querySelector(".why");
        Array.prototype.forEach.call(q.querySelectorAll("label.opt"), function (l) {
          l.classList.remove("is-wrong");
        });
        label.classList.add(isRight ? "is-right" : "is-wrong");
        if (isRight) {
          q.classList.add("is-correct");
        }
        if (feedback) feedback.textContent = (isRight ? "Correct. " : "Not quite. ") + (why ? why.textContent : "");
        update();
      });
    });
    update();
  }
  function start() { Array.prototype.forEach.call(document.querySelectorAll("[data-quiz]"), init); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
