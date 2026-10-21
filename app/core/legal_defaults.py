"""Standard Privacy Policy and Terms of Service, published until an administrator saves their own text
(Admin -> Settings -> Legal pages). Written for a Nigerian church under the Nigeria Data Protection Act 2023 (NDPA);
the church should have its own adviser confirm the wording, especially the retention periods."""
from __future__ import annotations

from html import escape

LEGAL_UPDATED = "September 2026"


def _who(site_name: str, address: str, email: str, phone: str) -> str:
    bits = [f"<strong>{escape(site_name)}</strong>"]
    if address:
        bits.append(escape(address))
    contact = []
    if email:
        contact.append(f'<a href="mailto:{escape(email)}">{escape(email)}</a>')
    if phone:
        contact.append(escape(phone))
    if contact:
        bits.append(" · ".join(contact))
    return "<br/>".join(bits)


def privacy_html(site_name: str, address: str = "", email: str = "", phone: str = "") -> str:
    n = escape(site_name)
    reach = f'<a href="mailto:{escape(email)}">{escape(email)}</a>' if email else "the contact details below"
    return f"""
<p><em>Last updated: {LEGAL_UPDATED}</em></p>
<p>{n} (“we”, “us”) is a Christian ministry in Nigeria. This page explains what personal information we collect through this website, why, who else sees it, how long we keep it, and the choices you have. We handle personal data in line with the Nigeria Data Protection Act 2023 (NDPA).</p>

<h2>Who is responsible for your information</h2>
<p>{_who(site_name, address, email, phone)}</p>
<p>Each of our branches receives the messages and requests that are addressed to it, and keeps them for that branch’s pastoral work. Headquarters can see what its own team receives; a branch cannot see another branch’s requests.</p>

<h2>What we collect, and why</h2>
<ul>
<li><strong>Contact messages and visit plans</strong> — your name, email, phone (optional), the message, and the branch you choose. We use it to reply to you and to welcome you. <em>Basis: your request / consent.</em></li>
<li><strong>Prayer requests</strong> — your request, and your name, email and phone if you give them (you may submit anonymously). It is read only by the prayer and pastoral team of the branch you selected. We do not publish it. If you tick “let me know”, we email you when it has been prayed over. <em>Basis: your consent. Because a prayer request can reveal religious belief or health, we treat it as sensitive and limit who can see it.</em></li>
<li><strong>Newsletter and devotional emails</strong> — your email address. We send updates you asked for, and every email has an unsubscribe link. <em>Basis: consent.</em></li>
<li><strong>Event reminders</strong> — your email address and the event you chose, used once to remind you. <em>Basis: consent.</em></li>
<li><strong>Giving</strong> — your name, email, phone, the amount and purpose, and a payment reference. Card and bank details are entered on our payment providers’ pages (Paystack or Flutterwave); we never see or store them. We keep giving records to issue receipts and to meet our accounting and legal duties. <em>Basis: performing your gift instruction and our legal obligations.</em></li>
<li><strong>Visit statistics</strong> — we count page views, the site you came from and the type of device, without cookies, without storing your IP address, and without profiling you. The counts cannot identify you. <em>Basis: our legitimate interest in knowing which pages help people.</em></li>
<li><strong>Security cookie</strong> — a small cookie keeps forms safe from forgery and remembers a message shown once. It is not used for tracking or advertising and expires within a day.</li>
</ul>

<h2>Who receives your information</h2>
<ul>
<li>Our staff and volunteers, only as far as their role needs.</li>
<li>Service providers who work for us under agreement: our payment providers (Paystack, Flutterwave), our email delivery provider (Resend), and our hosting provider. Some of these process data outside Nigeria; where they do, we rely on the safeguards the NDPA allows.</li>
<li>Authorities, where the law requires it.</li>
</ul>
<p>We do not sell your information and we do not share it for advertising.</p>

<h2>How long we keep it</h2>
<ul>
<li>Contact messages and visit plans: only as long as needed to reply and follow up — normally no more than 24 months after we last correspond — then deleted or anonymised.</li>
<li>Prayer requests: until the prayer team has followed up — normally no more than 24 months — unless you ask us to keep it. You can ask us to delete yours at any time.</li>
<li>Newsletter address: until you unsubscribe.</li>
<li>Event reminders: removed shortly after the event has taken place.</li>
<li>Giving records: as long as our accounting and legal duties require (normally at least six years).</li>
</ul>

<h2>Your rights</h2>
<p>You may ask us to tell you what we hold about you, correct it, delete it, restrict or stop using it, give you a copy, or withdraw a consent you gave — at any time and without giving a reason. Write to {reach}. We will answer within a reasonable time, and no later than the period the NDPA sets. If you are not satisfied, you may complain to the Nigeria Data Protection Commission (NDPC).</p>

<h2>Children</h2>
<p>This website is meant for adults. If you are under 18, please involve a parent or guardian before you send us personal information. If a child’s information reaches us without a parent’s consent, tell us and we will delete it.</p>

<h2>Security</h2>
<p>Our site runs over an encrypted connection, access to records is limited by role and by branch, sign-ins are protected, and we record who accesses the administration area. No system is perfectly secure; if a breach affects you, we will tell you and the NDPC as the law requires.</p>

<h2>Links to other sites</h2>
<p>Our pages may link to social networks, payment pages and streaming platforms. They have their own privacy practices, which we do not control.</p>

<h2>Changes</h2>
<p>If we change how we use your information we will update this page and its date. Material changes will be announced on the site.</p>

<h2>Contact</h2>
<p>{_who(site_name, address, email, phone)}</p>
"""


def terms_html(site_name: str, address: str = "", email: str = "", phone: str = "") -> str:
    n = escape(site_name)
    return f"""
<p><em>Last updated: {LEGAL_UPDATED}</em></p>
<p>Welcome to the website of {n}. By using it you agree to these terms. If you do not agree, please do not use the site.</p>

<h2>Using the site</h2>
<p>You may browse, listen to and share our public content for personal, non-commercial and ministry use. Please do not misuse the site: no attempts to break into it, overload it, copy its data in bulk, or use it to send spam or unlawful, abusive or misleading material.</p>

<h2>Our content</h2>
<p>The messages, articles, images, logo and design belong to {n} or are used with permission. You may share a link, and quote short extracts with credit. Please do not sell our recordings, re-upload them as your own, or change them and present them as ours without written permission.</p>

<h2>What you send us</h2>
<p>When you send a prayer request, message or story, you confirm that it is yours to send and that it is truthful and lawful. We use it only as described in our Privacy Policy. We may decline or remove anything that is abusive, unlawful or unsuitable.</p>

<h2>Giving</h2>
<ul>
<li>Gifts are voluntary and given to support the work of the ministry and the purpose you choose. Where you choose a branch, the gift is recorded for that branch.</li>
<li>Payments are processed by Paystack or Flutterwave. Their terms apply to the payment itself.</li>
<li>We send a receipt to the email you provide. If something looks wrong — a double charge, a wrong amount — contact us promptly with your payment reference and we will look into it. Because a gift is a free-will offering, refunds are considered case by case.</li>
<li>Gifts marked for a specific purpose are used for that purpose; if a project is fully funded or cannot go ahead, the ministry may apply the gift to a closely related need.</li>
</ul>

<h2>Advice and health</h2>
<p>Teaching, counselling and prayer offered on this site are spiritual encouragement. They are not medical, legal or financial advice, and are not a substitute for a qualified professional. In an emergency, contact the emergency services.</p>

<h2>Links and availability</h2>
<p>We link to other sites for your convenience and are not responsible for them. We work to keep the site available and accurate, but we provide it “as is” and may change or suspend it at any time.</p>

<h2>Limit of liability</h2>
<p>To the extent the law allows, {n} is not liable for indirect or consequential loss arising from your use of the site. Nothing in these terms limits any right you have under Nigerian consumer or data-protection law.</p>

<h2>Governing law</h2>
<p>These terms are governed by the laws of the Federal Republic of Nigeria. We would like to resolve any concern with you directly first.</p>

<h2>Changes</h2>
<p>We may update these terms; the date above shows the latest version. Continuing to use the site after a change means you accept it.</p>

<h2>Contact</h2>
<p>{_who(site_name, address, email, phone)}</p>
"""
