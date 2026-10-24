"""Finto MediaPlayer: registra le pubblicazioni nel Centro di Controllo."""
MPRemoteCommandHandlerStatusSuccess = 0
MPNowPlayingPlaybackStatePlaying, MPNowPlayingPlaybackStatePaused = 1, 2
(MPMediaItemPropertyTitle, MPMediaItemPropertyArtist, MPMediaItemPropertyAlbumTitle,
 MPMediaItemPropertyPlaybackDuration, MPNowPlayingInfoPropertyElapsedPlaybackTime,
 MPNowPlayingInfoPropertyPlaybackRate, MPMediaItemPropertyArtwork) = (
    "title", "artist", "album", "duration", "elapsed", "rate", "artwork")


class _Comando:
    def __init__(self, nome):
        self.nome, self.gestori = nome, []

    def setEnabled_(self, v):
        pass

    def addTargetWithHandler_(self, f):
        self.gestori.append(f)
        return object()


class MPRemoteCommandCenter:
    _c = None

    @classmethod
    def sharedCommandCenter(cls):
        if cls._c is None:
            cls._c = cls()
            for n in ("togglePlayPauseCommand", "playCommand", "pauseCommand", "nextTrackCommand",
                      "previousTrackCommand", "changePlaybackPositionCommand"):
                setattr(cls._c, n, (lambda c: (lambda: c))(_Comando(n)))
        return cls._c


class MPNowPlayingInfoCenter:
    _c = None

    @classmethod
    def defaultCenter(cls):
        if cls._c is None:
            cls._c = cls()
            cls._c.info, cls._c.stato, cls._c.pubblicazioni = None, None, 0
        return cls._c

    def setNowPlayingInfo_(self, d):
        self.info = dict(d)
        self.pubblicazioni += 1

    def nowPlayingInfo(self):
        return self.info

    def setPlaybackState_(self, s):
        self.stato = s


class MPMediaItemArtwork:
    @classmethod
    def alloc(cls):
        return cls()

    def initWithBoundsSize_requestHandler_(self, dim, f):
        self.f = f
        return self
