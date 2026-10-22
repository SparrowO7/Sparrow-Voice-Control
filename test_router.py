import router

cases = [
    ('yt search for channel and play 1st video', 'youtube_channel'),
    ('youtube pe music search karo', 'youtube_channel'),
    ('yt pe harsh tiwari search karo', 'youtube_channel'),
    ('youtube pe cooking channel dikhao', 'youtube_channel'),
    ('food4all ka doc kholo', 'open_file'),
    ('chrome kholo', 'open_app'),
    ('google karo best laptop', 'web_search'),
    ('yt pe coding channel dikhao', 'youtube_channel'),
    ('open youtube search for tech channel', 'youtube_channel'),
    ('chrome kholo aur google karo', 'web_search'),
]

for text, expected in cases:
    i = router.route(text)
    status = 'OK' if i.name == expected else 'MISS'
    print(f'{status} {text!r} -> {i.name}/{i.term} (want {expected})')