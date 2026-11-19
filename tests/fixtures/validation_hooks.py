def accept(candidate, result):
    return True


def reject(candidate, result):
    assert 'password' not in candidate
    return False


def broken(candidate, result):
    raise RuntimeError('private diagnostic must not be returned')
