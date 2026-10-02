"""Chapter 5: the database."""
from figs import KINDS, Fig


def _table(f, x, y, w, name, fields, kind):
    st, fl, tx = KINDS[kind]
    h = 26 + len(fields) * 14 + 6
    f.rect(x, y, w, h, kind, r=6, fill='#ffffff')
    f.raw(f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="6" ry="6" fill="{st}"/>')
    f.raw(f'<rect x="{x}" y="{y + 14}" width="{w}" height="10" fill="{st}"/>')
    f.text(x + 8, y + 17, name, 11.5, 'bold', color='#ffffff', anchor='start', mono=True)
    for i, (fld, note) in enumerate(fields):
        yy = y + 40 + i * 14
        bold = fld.startswith('*')
        f.text(x + 8, yy, fld.lstrip('*'), 9.5, 'bold' if bold else 'normal', anchor='start', mono=True,
               color=tx if bold else '#334155')
        if note:
            f.text(x + w - 6, yy, note, 8.5, anchor='end', kind='ext', italic=True)
    return h


def er_schema() -> str:
    f = Fig(780, 600)
    f.text(20, 20, 'Схема базы: 10 таблиц (жирным — ключи; стрелка — внешний ключ «многие → один»)', 12, 'bold',
           anchor='start')
    _table(f, 20, 40, 150, 'channels', [('*id', ''), ('username', ''), ('topic', ''), ('subscribers', ''),
                                         ('last_message_id', 'курсор'), ('last_synced_at', '')], 'col')
    _table(f, 205, 40, 180, 'posts', [('*id', ''), ('channel_id', '→ channels'), ('tg_message_id', 'уник. пара'),
                                       ('posted_at', ''), ('text', ''), ('media_type', ''), ('media_path', ''),
                                       ('views, reactions_count', ''), ('grouped_id', 'альбом'),
                                       ('forward_from_*', 'пересылка'), ('raw_json', 'JSONB')], 'col')
    _table(f, 420, 40, 190, 'enrichment', [('*post_id', '1 : 1 posts'), ('ocr_text', ''), ('vlm_caption', ''),
                                            ('topic_labels', 'JSONB'), ('is_ad, is_spoiler', ''), ('quality_score', ''),
                                            ('injection_flag', ''), ('entities_json', 'JSONB'),
                                            ('external_links', 'JSONB'), ('link_summary', ''),
                                            ('model_version', 'кто и чем')], 'ing')
    _table(f, 635, 40, 130, 'entity_cache', [('*provider', ''), ('*query', ''), ('result_json', ''),
                                              ('fetched_at', '')], 'ing')
    _table(f, 205, 300, 180, 'post_signatures', [('*post_id', '1 : 1 posts'), ('text_hash', 'sha1'),
                                                 ('file_sha256', ''), ('phash', '64 бита')], 'ing')
    _table(f, 420, 300, 190, 'post_clusters', [('*post_id', '→ posts'), ('*cluster_id', '→ clusters'),
                                                ('stage', 'text|file|phash…')], 'ing')
    _table(f, 635, 300, 130, 'clusters', [('*id', ''), ('topic', ''), ('representative_', ''), ('  post_id', '→ posts'),
                                          ('size', '')], 'ing')
    _table(f, 20, 430, 150, 'user_profile', [('*user_id', ''), ('topic_weights_json', ''), ('channel_affinity_', ''),
                                              ('interest_centroids', ''), ('stats_json', '')], 'dig')
    _table(f, 205, 430, 180, 'feedback', [('*id', ''), ('user_id', ''), ('post_id', '→ posts'), ('signal', 'like…'),
                                           ('context', 'search|digest'), ('digest_id', '→ digests')], 'dig')
    _table(f, 420, 430, 190, 'digests', [('*id', ''), ('user_id', ''), ('plan_json', ''), ('items_json', ''),
                                          ('critic_iterations', ''), ('trace_id', '→ Langfuse')], 'dig')
    # relations
    f.line(205, 94, 172, 94, 'col', sw=1.4)              # posts.channel_id -> channels
    f.line(420, 94, 387, 94, 'ing', sw=1.4)              # enrichment -> posts
    f.line(295, 300, 295, 230, 'ing', sw=1.4)            # signatures -> posts
    f.path('M420,340 C400,340 395,260 387,220', 'ing', sw=1.4)   # post_clusters -> posts
    f.line(610, 354, 633, 354, 'ing', sw=1.4)            # post_clusters -> clusters
    f.path('M700,300 C700,262 420,262 387,230', 'ing', sw=1.2, dash=True)  # representative
    f.path('M205,510 C150,410 180,260 203,200', 'dig', sw=1.4)   # feedback.post_id -> posts
    f.line(385, 528, 418, 528, 'dig', sw=1.4)            # feedback.digest_id -> digests
    f.text(20, 590, 'user_id в feedback и digests связан с user_profile по смыслу, без внешнего ключа: голос может '
           'прийти раньше профиля.', 10, anchor='start', kind='ext', italic=True)
    return f.render()
