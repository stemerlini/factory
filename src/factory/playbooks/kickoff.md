# Kickoff

Goal: from the user's idea to a project they recognise, in one sitting, without guessing.

1. **Interview.** Read the idea. Ask what you need to know to describe the product, at
   most five questions at a time, the most important first: what it is for, who uses it,
   where it must work (environment, lifetime), what it must achieve (numbers), what
   constrains it (budget, mass, power, schedule, standards), and what already exists.
   Stop asking when the answers stop changing what you would write.
2. **Describe the system.** Rewrite the root `element.md` from the answers: what the
   product is, what it does, its boundaries. Keep the user's words where they are clear.
3. **Needs to requirements.** Write the five to ten requirements that drive the design in
   `req/` (FORMATS.md): one testable "shall" each, with a method, and a `criterion` on a
   metric where a study will prove it.
4. **First architecture.** A first cut of `architecture.yaml`: the main blocks, their
   interfaces, which block meets which requirement. Don't create subsystem folders yet;
   say which blocks deserve one.
5. **Open choices.** Every real trade-off you met (technology, orbit, margin, scope)
   becomes a question with options and a recommendation: `factory ask`.
6. **Hand over.** `factory check`, then write `.factory/proposal.md`: what you drafted,
   what you assumed, the questions waiting, and the next three steps. The user reviews it
   all in the Review tab.
