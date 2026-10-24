"""Finto Foundation: registra le operazioni di disegno in TELA.ops."""
from types import SimpleNamespace as NS


class _Tela:
    def __init__(self):
        self.azzera()

    def azzera(self):
        self.ops = []
        self.scala = 1.0
        self.riempimento = None
        self.tratto = None
        self.clip = None          # (x0, y0, x1, y1) in punti
        self.pila = []

    def pt(self, x, y):
        return (x * self.scala, y * self.scala)

    def rett(self, r):
        x, y, w, h = _xywh(r)
        s = self.scala
        return (x * s, y * s, (x + w) * s, (y + h) * s)


TELA = _Tela()


def _xywh(r):
    if isinstance(r, Rect):
        return r.x, r.y, r.w, r.h
    (x, y), (w, h) = r
    return x, y, w, h


def _xy(p):
    return (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])


class Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)
        self.origin = NS(x=self.x, y=self.y)
        self.size = NS(width=self.w, height=self.h)


def NSMakeRect(x, y, w, h):
    return Rect(x, y, w, h)


class NSObject:
    def __init_subclass__(cls, protocols=None, **kw):
        # Come PyObjC 12 reale: dichiarare protocolli formali per i delegati fa
        # fallire la validazione delle firme BOOL ('Z' dei metadati contro 'B'
        # del protocollo). Il banco rifiuta quindi qualunque `protocols=`.
        if protocols:
            raise TypeError(f"{cls.__name__}: non dichiarare protocols=; i delegati "
                            "sono già noti a PyObjC dai metadati (vedi README-progetto §4)")
        super().__init_subclass__(**kw)

    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    def init(self):
        return self

    def respondsToSelector_(self, sel):
        return hasattr(self, sel.replace(":", "_"))


class NSAttributedString(NSObject):
    def initWithString_attributes_(self, s, attr):
        self.s, self.attr = s, attr
        return self

    def initWithString_(self, s):
        return self.initWithString_attributes_(s, {})

    def size(self):
        f = self.attr.get("NSFont")
        dim = f.dim if f else 12.0
        kern = self.attr.get("NSKern", 0.0)
        return NS(width=len(self.s) * (0.602 * dim + kern), height=dim * 1.17)

    def drawAtPoint_(self, p):
        x, y = _xy(p)
        f = self.attr.get("NSFont")
        TELA.ops.append(("testo", self.s, TELA.pt(x, y), f.dim * TELA.scala, f.grassetto,
                         self.attr.get("NSColor"), self.attr.get("NSKern", 0.0) * TELA.scala, TELA.clip))


class NSAffineTransform(NSObject):
    @classmethod
    def transform(cls):
        t = cls()
        t.s = 1.0
        return t

    def scaleBy_(self, s):
        self.s *= s

    def concat(self):
        TELA.scala *= self.s


class NSTimer(NSObject):
    creati = []

    @classmethod
    def timerWithTimeInterval_target_selector_userInfo_repeats_(cls, i, t, sel, u, r):
        cls.creati.append((i, t, sel))
        return cls()


class NSRunLoop(NSObject):
    @classmethod
    def currentRunLoop(cls):
        return cls()

    def addTimer_forMode_(self, t, m):
        pass


NSRunLoopCommonModes = "common"


class NSURL(NSObject):
    @classmethod
    def URLWithString_(cls, s):
        u = cls()
        u.s = s
        return u

    def host(self):
        return self.s.split("/")[2] if "//" in self.s else ""

    def scheme(self):
        return self.s.split(":")[0]


class NSURLRequest(NSObject):
    @classmethod
    def requestWithURL_(cls, u):
        r = cls()
        r.u = u
        return r

    def URL(self):
        return self.u


class NSURLSession(NSObject):
    richieste = []

    @classmethod
    def sharedSession(cls):
        return cls()

    def dataTaskWithURL_completionHandler_(self, url, gestore):
        NSURLSession.richieste.append(url.s)
        return NS(resume=lambda: gestore(b"PNG", None, None))


class NSBundle(NSObject):
    @classmethod
    def mainBundle(cls):
        return cls()

    def resourcePath(self):
        return None
