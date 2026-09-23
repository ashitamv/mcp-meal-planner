# Attribution and external data

The capstone concept, original operation names and five prompt intents come from
Paulo Dichone's **Model Context Protocol Unlocked From Fundamentals to Advanced
Customization**, published by Packt. The implementation in `src/recipe_mcp` was
written from scratch around those requirements; the publisher's project is not
bundled. Source reviewed at commit `12ae65cf869c1105aa102b917f19a95fbaee87d2`:

https://github.com/PacktPublishing/Model-Context-Protocol-Unlocked-From-Fundamentals-to-Advanced-Customization

The upstream repository is MIT licensed. Its license is retained in
`docs/COURSE_LICENSE.txt` for attribution.

Runtime and developer dependencies retain their own licenses and notices. Their
resolved versions appear in `uv.lock` and the runtime export `requirements.lock`.

Live recipe text and image/source links are supplied by TheMealDB and their
original contributors. The application code license does not grant ownership of
that data. Display attribution and original source links when presenting live
recipes. Consult https://www.themealdb.com/api.php for API-key usage guidance.

The six offline recipes in `demo_data.json` are original illustrative fixtures
included with this project. They are not sourced from the course's downloaded
API data and do not provide verified nutrition, serving counts or allergy safety.
