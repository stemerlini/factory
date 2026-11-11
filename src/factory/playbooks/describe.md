# Describe

Goal: anyone can tell what this element is for and how we will know it works.

1. Read the parent's description and requirements (`factory req -e <parent>`) and the
   accepted decisions. Ask the user what you cannot infer; do not invent needs.
2. Write `element.md`: a paragraph on what the element is, what it does, and its
   boundaries (what it is not).
3. Write requirements in `req/` (one file each, FORMATS.md). Each is one testable
   "shall", with a method. Analysed ones get a `criterion` on a metric a study will
   report. In a subsystem, set `parent:` to the requirement it is derived from.
4. Keep the set small: the five to ten that drive the design. List open needs as
   questions in the proposal, or as `factory ask` when there are real options.
5. `factory check`, then hand over.
