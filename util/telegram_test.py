from util import notifier


def run_tests():
    samples = [
        "<b>종목</b>: 1441, 1450, 1592, 1601",
        "종목: *1441, 1450, 1592, 1601*",
        "번호 1441, 1450, 1592, 1601: 확인 필요",
    ]

    modes = ['HTML', 'MarkdownV2', None]

    for s in samples:
        print('---')
        print('Original:', s)
        for m in modes:
            payload = notifier.format_message_for_telegram(s, parse_mode=m)
            print(f'parse_mode={m!r} -> {payload["text"]}')


if __name__ == '__main__':
    run_tests()
