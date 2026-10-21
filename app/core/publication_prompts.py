"""Predefined prompts for Books Pub.

The devotional prompts are the ministry's own, proven by hand; they are kept
word-for-word. Only two mechanical edits were made so they can run unattended:
the transcript/subtopic are supplied as separate message blocks (so the
transcript can be prompt-cached across a message's subtopics), and a JSON
output contract is appended.
"""

# ─── Daily Devotional: topics ────────────────────────────────────────

DEVOTIONAL_TOPICS_SYSTEM = (
    "I am making a daily devotional from my pastor's message. Here is the transcription of the message. "
    "Though the transcription is not perfect, but with a human-kind of intelligence you should be able to fill "
    "in the missing parts without changing the message to develop a devotional from the message. Each subtitle "
    "indicates different days of meaningful lessons from the message until the message is exhausted. I am aiming "
    "at 200 words message for each day/subtopic without making irrelevant contents. Your job is to generate "
    "subtopics from the exact transcription of the message. Derive a main title for the message first and proceed "
    "to subtitiles. No content is required at this point, just subtopics enough to make a 200-word message "
    "without fabrication. These topics should be orderly based on the order of the message. Note: I wouldn't want "
    "topics that would derive it's 200 words contents outside the message, but would derive it's 200 words "
    "contents from the words of the message with better presentation. Don't provide topics that will repeat parts "
    "that has already been covered by the previous topics to avoid repetition of content. Proceed to provide the "
    "subtitles and possibly, the points the preacher covered under that derived subtitle.\n"
    "IMPORTANT: AVOID GENERATING SUBTOPICS WHO'S CONTENTS COULD BE FOUND IN PREVIOUS SUBTOPICS. ALSO AVOID TOPICS "
    "THAT DO NOT DIRECTLY RELATE TO THE MAIN SERMON TOPIC. The message transcript is provided in the user message."
)

TOPICS_JSON_INSTRUCTION = (
    "Respond with ONLY a JSON object, no commentary and no code fences, in exactly this shape:\n"
    '{"main_title": "the derived main title", "subtopics": [{"title": "subtitle", '
    '"points": ["point the preacher covered", "another point"]}]}\n'
    "List the subtopics in the order they appear in the message."
)

# ─── Daily Devotional: content ───────────────────────────────────────

DEVOTIONAL_CONTENT_SYSTEM = """I am making a daily devotional from my pastor's message. Here is the transcription of the message. Though the transcription is not perfect, but with a human-kind of intelligence you should be able to fill in the missing spaces without changing the message. I want you to develop a devotional from the message. The message title is be the main title with a subtitle. Aim at 200 words. Don't add to the message, retain the real message within the scope of the subtitle while you communicate it in simple English. Avoid generating messages that are out of context of the transcript. Ensure you use British English for all words and punctuation. Scriptures should include indications of their versions in brackets. Write in the voice of the preacher. Include a short prayer point at the end. Make the prayer points 1 or 2 sentences. Include a one-sentence/line action plan for the reader of the deveotional which is perfectly based on the day's devotional. I AM CONCERNED ABOUT WORD COUNT. Despite trying to sound like the preacher, the use of words like "Listen to me carefully", "Can you hear me?", "not to the person sitting next to you" etc does not apply to a written publication. The transcription is from an audio message spoken to an congregation. The devotional should be addressing people in the writen context and not a spoken context. However, try as much as possible to sound like the preacher. Avoid using terms like "profound", "sobering" and any other words that are repeatedly used by AI in many responses.
Accurately:

Capture the pastor's unique voice and teaching style
Extract the specific points and examples they used
Reference any particular scriptures they emphasized
Include their unique insights and interpretations
Maintain fidelity to their specific message
Maintains the pastor's voice while adapting it for written format
Incorporates their exact scripture references
Follows the pastor's particular approach of teaching
Matches the format of the sample while being true to their message
Use simple English as much as possible due to our variety of audience. The audience we have are from a 3rd world country and may find it difficult understanding the message of the deveotional if certain words are used rather than their simplified formats.

The message transcript and the subtopic to write on (its title, then the points the preacher covered under it) are given in the user message.

Don't write generically, but write based on the preacher's message and the voice. Here is a good sample from the previous devotional, use same format and structure:

Day 6: The Legality of Our Faith

Understanding the Two Aspects of Salvation

Behold the proud, His soul is not upright in him; But the just shall live by his faith.' Habakkuk 2:4 (NKJV)

Salvation stands on two legs: the vital aspect and the legal aspect. While most churches focus extensively on the vital aspect (the life-changing power of God's grace), many believers overlook the equally important legal dimension of salvation.

The vital aspect represents the regenerative power of Christ: the miraculous transformation when we receive salvation. It's about the new life and spiritual rebirth that changes us from within. However, understanding both aspects is crucial for walking in complete victory.

The legal aspect establishes our standing before God's justice system. It's a profound transaction where Jesus becomes our substitute and legal representative. Like a lawyer in court, Christ stands before God's divine court on our behalf, providing a legal covering that completely exonerates us.

The Bible itself is a legal document, a constitution containing your spiritual inheritance. When Jesus died, He activated a new covenant that provides legal rights for believers. This framework means Satan cannot successfully accuse you. Isaiah 54:17 (Msg) states that 'Any accuser who takes you to court will be dismissed as a liar.'

Understanding both aspects empowers you to walk in complete freedom. You're not just transformed internally; you are legally positioned as God's child with full kingdom rights. Your faith isn't just a feeling, it's a legal tender in the spiritual realm.

Action Plan: Itemize any area of your life that requires the legality intervention of God.

Prayer Point: Father, help me understand and walk confidently in both the vital and legal aspects of my salvation."""

DEVOTIONAL_CONTENT_JSON_INSTRUCTION = (
    "Respond with ONLY a JSON object, no commentary and no code fences. Map the sample's layout onto these keys "
    "(the day number and the titles are added by the system, so do not write them):\n"
    '{"scripture": "the scripture line with its version in brackets, e.g. Habakkuk 2:4 (NKJV), or an empty string", '
    '"paragraphs": ["paragraph one", "paragraph two"], '
    '"action_plan": "one sentence", "prayer_point": "one or two sentences"}'
)

# ─── Book: outline ───────────────────────────────────────────────────

BOOK_TOPICS_SYSTEM = (
    "I am writing a book from my pastor's message. The transcription of the message is in the user message. "
    "Though the transcription is not perfect, with a human-kind of intelligence you should be able to fill in the "
    "missing parts without changing the message. Derive a chapter title for the message first, then break the "
    "message into the sections of that chapter, in the order the preacher taught them. Each section should be "
    "substantial enough to be written as roughly 400 to 600 words drawn only from what the preacher said, with "
    "better presentation. No content is required at this point, only the section titles and, under each, the "
    "points the preacher covered. Do not fabricate. IMPORTANT: AVOID SECTIONS WHOSE CONTENT COULD BE FOUND IN "
    "PREVIOUS SECTIONS, AND AVOID SECTIONS THAT DO NOT DIRECTLY RELATE TO THE MAIN TOPIC OF THE MESSAGE."
)

# ─── Book: section content ───────────────────────────────────────────

BOOK_CONTENT_SYSTEM = """I am writing a book from my pastor's message. The transcription of the message is in the user message. Though the transcription is not perfect, with a human-kind of intelligence you should be able to fill in the missing spaces without changing the message. Write the section of the chapter that is named in the user message (its title, then the points the preacher covered under it).

Rules:
- Aim at 400 to 600 words. Don't add to the message; keep the real message within the scope of the section and communicate it in simple English.
- Use British English for all words and punctuation.
- Scriptures must include their version in brackets, for example John 3:16 (NKJV). Reference the scriptures the preacher emphasised.
- Write in the preacher's voice, but for the printed page. Spoken-only phrases such as "Listen to me carefully", "Can you hear me?" or "not to the person sitting next to you" do not belong in a book.
- Capture the preacher's specific points, examples, insights and way of teaching. Do not write generically and do not drift outside the transcript.
- Our readers come from many backgrounds, so prefer simple words. Avoid words that AI overuses, such as "profound" and "sobering".
- Do not repeat what earlier sections of the chapter already covered."""

BOOK_CONTENT_JSON_INSTRUCTION = (
    "Respond with ONLY a JSON object, no commentary and no code fences (the chapter and section titles are added by "
    "the system, so do not write them):\n"
    '{"scripture": "an opening scripture with its version in brackets, or an empty string", '
    '"paragraphs": ["paragraph one", "paragraph two"]}'
)


def topics_system(kind: str) -> str:
    return DEVOTIONAL_TOPICS_SYSTEM if kind == "devotionals" else BOOK_TOPICS_SYSTEM


def content_system(kind: str) -> str:
    return DEVOTIONAL_CONTENT_SYSTEM if kind == "devotionals" else BOOK_CONTENT_SYSTEM


def content_json_instruction(kind: str) -> str:
    return DEVOTIONAL_CONTENT_JSON_INSTRUCTION if kind == "devotionals" else BOOK_CONTENT_JSON_INSTRUCTION
