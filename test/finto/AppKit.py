"""Finto AppKit: finestre, viste, menu e primitive di disegno registrate."""
from types import SimpleNamespace as NS

from Foundation import TELA, NSObject, Rect, _xy, _xywh

NSFontAttributeName, NSForegroundColorAttributeName, NSKernAttributeName = "NSFont", "NSColor", "NSKern"
NSBackingStoreBuffered = 2
NSViewWidthSizable, NSViewHeightSizable = 2, 16
NSApplicationActivationPolicyRegular = 0
NSAppearanceNameDarkAqua = "dark"
NSFocusRingTypeNone = 1


class NSColor:
    def __init__(self, r, g, b, a):
        self.rgba = (r, g, b, a)

    @classmethod
    def colorWithSRGBRed_green_blue_alpha_(cls, r, g, b, a):
        return cls(r, g, b, a)

    @classmethod
    def blackColor(cls):
        return cls(0, 0, 0, 1)

    def setFill(self):
        TELA.riempimento = self

    def setStroke(self):
        TELA.tratto = self


class NSFont:
    def __init__(self, nome, dim):
        self.nome, self.dim, self.grassetto = nome, dim, "Bold" in nome

    @classmethod
    def fontWithName_size_(cls, nome, dim):
        return cls(nome, dim)

    @classmethod
    def monospacedSystemFontOfSize_weight_(cls, dim, peso):
        return cls("Mono", dim)

    def pointSize(self):
        return self.dim


class NSBezierPath:
    def __init__(self):
        self.tratti, self.corrente, self.spessore, self.arrotondato = [], None, 1.0, None

    @classmethod
    def bezierPath(cls):
        return cls()

    @classmethod
    def bezierPathWithRoundedRect_xRadius_yRadius_(cls, r, rx, ry):
        p = cls()
        p.arrotondato = (TELA.rett(r), rx * TELA.scala)
        return p

    @classmethod
    def fillRect_(cls, r):
        TELA.ops.append(("rett", TELA.rett(r), TELA.riempimento, TELA.clip))

    @classmethod
    def clipRect_(cls, r):
        x0, y0, x1, y1 = TELA.rett(r)
        if TELA.clip:
            a0, b0, a1, b1 = TELA.clip
            x0, y0, x1, y1 = max(x0, a0), max(y0, b0), min(x1, a1), min(y1, b1)
        TELA.clip = (x0, y0, x1, y1)

    def moveToPoint_(self, p):
        self.corrente = [TELA.pt(*_xy(p))]
        self.tratti.append(self.corrente)

    def lineToPoint_(self, p):
        self.corrente.append(TELA.pt(*_xy(p)))

    def closePath(self):
        pass

    def setLineWidth_(self, w):
        self.spessore = w

    def stroke(self):
        if self.arrotondato:
            TELA.ops.append(("rrett_bordo", self.arrotondato, TELA.tratto, self.spessore * TELA.scala))
        else:
            TELA.ops.append(("linee", self.tratti, TELA.tratto, self.spessore * TELA.scala, TELA.clip))

    def fill(self):
        if self.arrotondato:
            TELA.ops.append(("rrett", self.arrotondato, TELA.riempimento))
        else:
            for t in self.tratti:
                TELA.ops.append(("poligono", t, TELA.riempimento, TELA.clip))


class NSGradient:
    @classmethod
    def alloc(cls):
        return cls()

    def initWithColors_(self, colori):
        self.colori = list(colori)
        return self

    def drawInRect_angle_(self, r, angolo):
        TELA.ops.append(("gradiente", TELA.rett(r), self.colori, angolo, TELA.clip))


class NSGraphicsContext:
    @classmethod
    def saveGraphicsState(cls):
        TELA.pila.append(TELA.clip)

    @classmethod
    def restoreGraphicsState(cls):
        TELA.clip = TELA.pila.pop()


class NSEvent:
    posizione_mouse = NS(x=0.0, y=0.0)

    @classmethod
    def mouseLocation(cls):
        return NS(x=cls.posizione_mouse.x, y=cls.posizione_mouse.y)


class NSView(NSObject):
    def initWithFrame_(self, cornice):
        self._cornice = cornice
        self._finestra = None
        self.ridisegni = 0
        self.figli = []
        return self

    def addSubview_(self, v):
        self.figli.append(v)
        v._finestra = self._finestra

    def bounds(self):
        return Rect(0, 0, self._cornice.w, self._cornice.h)

    def setNeedsDisplay_(self, v):
        self.ridisegni += 1

    def window(self):
        return self._finestra

    def convertPoint_fromView_(self, p, v):
        return NS(x=p[0], y=p[1])

    def setAutoresizingMask_(self, m):
        pass

    def keyDown_(self, ev):
        NSView.beep = getattr(NSView, "beep", 0) + 1


class _Editor:
    colore = None

    def respondsToSelector_(self, s):
        return True

    def setInsertionPointColor_(self, c):
        self.colore = c


class NSTextField(NSView):
    """Campo di testo: valore, azione, delegato; i setter di stile sono ignorati."""

    def initWithFrame_(self, cornice):
        NSView.initWithFrame_(self, cornice)
        self._testo, self.font_, self.azione, self.obiettivo, self.delegato = "", None, None, None, None
        self.editor = _Editor()
        return self

    def cell(self):
        return NS(**{n: (lambda *a: None) for n in ("setUsesSingleLineMode_", "setScrollable_",
                                                    "setSendsActionOnEndEditing_")})

    def stringValue(self):
        return self._testo

    def setStringValue_(self, s):
        self._testo = s

    def setFrame_(self, r):
        self._cornice = r

    def frame(self):
        return self._cornice

    def setFont_(self, f):
        self.font_ = f

    def setTarget_(self, t):
        self.obiettivo = t

    def setAction_(self, a):
        self.azione = a

    def setDelegate_(self, d):
        self.delegato = d

    def becomeFirstResponder(self):
        return True

    def currentEditor(self):
        return self.editor

    def __getattr__(self, nome):
        if nome.startswith("set"):
            return lambda *a: None
        raise AttributeError(nome)


class NSWindow(NSObject):
    def initWithContentRect_styleMask_backing_defer_(self, r, maschera, b, d):
        self._f = Rect(r.x, r.y, r.w, r.h)
        self._visibile, self._chiave, self._livello, self.vista = False, False, 0, None
        self.primo = None
        return self

    def makeFirstResponder_(self, r):
        self.primo = r
        if hasattr(r, "becomeFirstResponder"):
            r.becomeFirstResponder()
        return True

    def frame(self):
        return Rect(self._f.x, self._f.y, self._f.w, self._f.h)

    def setFrameOrigin_(self, o):
        x, y = _xy(o)
        self._f = Rect(x, y, self._f.w, self._f.h)

    def setFrame_display_(self, r, d):
        x, y, w, h = _xywh(r)
        self._f = Rect(x, y, w, h)
        if self.vista is not None:
            self.vista._cornice = Rect(0, 0, w, h)

    def setContentView_(self, v):
        self.vista = v
        if hasattr(v, "_finestra"):
            v._finestra = self

    def makeKeyAndOrderFront_(self, x):
        self._visibile = self._chiave = True

    def orderFront_(self, x):
        self._visibile = True

    def orderOut_(self, x):
        self._visibile = self._chiave = False

    def isVisible(self):
        return self._visibile

    def isKeyWindow(self):
        return self._chiave

    def setDelegate_(self, d):
        self.delegato = d

    def _notifica(self, nome):
        d = getattr(self, "delegato", None)
        if d is not None and hasattr(d, nome):
            getattr(d, nome)(NS(object=lambda: self))

    def isMiniaturized(self):
        return getattr(self, "ridotta", False)

    def occlusionState(self):
        return 2 if self._visibile else 0

    def miniaturize_(self, x):          # come AppKit: notifica, poi la finestra lascia lo schermo
        if self.isMiniaturized():
            return
        self._notifica("windowWillMiniaturize_")
        self.ridotta, self._visibile, self._chiave = True, False, False

    def deminiaturize_(self, x):
        if not self.isMiniaturized():
            return
        self.ridotta, self._visibile, self._chiave = False, True, True
        self._notifica("windowDidDeminiaturize_")

    def setLevel_(self, l):
        self._livello = l

    ordinamenti = []                      # (finestra, modo, numero della finestra di riferimento)

    def windowNumber(self):
        return id(self) % 100000

    def orderWindow_relativeTo_(self, modo, numero):
        NSWindow.ordinamenti.append((self, modo, numero))

    def screen(self):
        return NSScreen()

    def __getattr__(self, nome):          # setTitle_, setDelegate_, setHasShadow_, …
        if nome.startswith(("set", "make")):
            return lambda *a: None
        raise AttributeError(nome)


class NSScreen:
    @classmethod
    def mainScreen(cls):
        return cls()

    @classmethod
    def screens(cls):
        return [cls()]

    def visibleFrame(self):
        return Rect(0, 25, 1512, 920)


class NSMenuItem(NSObject):
    def init(self):
        self.titolo, self.azione, self.tasto, self._tag, self._stato, self.sotto = "", None, "", 0, 0, None
        return self

    @classmethod
    def separatorItem(cls):
        return cls.alloc().init()

    def setSubmenu_(self, m):
        self.sotto = m

    def setKeyEquivalentModifierMask_(self, m):
        self.modificatori = m

    def setTag_(self, t):
        self._tag = t

    def tag(self):
        return self._tag

    def setTarget_(self, t):
        self.obiettivo = t

    def setState_(self, s):
        self._stato = s

    def action(self):
        return self.azione


class NSMenu(NSObject):
    aperti = []

    def init(self):
        self.voci = []
        return self

    def initWithTitle_(self, t):
        self.titolo = t
        return self.init()

    def addItem_(self, v):
        self.voci.append(v)

    def addItemWithTitle_action_keyEquivalent_(self, t, a, k):
        v = NSMenuItem.alloc().init()
        v.titolo, v.azione, v.tasto = t, a, k
        self.voci.append(v)
        return v

    @classmethod
    def popUpContextMenu_withEvent_forView_(cls, m, e, v):
        cls.aperti.append(m)


class NSAppearance:
    @classmethod
    def appearanceNamed_(cls, n):
        return n


class NSWorkspace:
    aperti = []

    @classmethod
    def sharedWorkspace(cls):
        return cls()

    def openURL_(self, u):
        NSWorkspace.aperti.append(u.s)

    def selectFile_inFileViewerRootedAtPath_(self, a, b):
        pass


class NSImage(NSObject):
    def initWithData_(self, d):
        return self

    def size(self):
        return NS(width=544, height=544)


class NSApplication(NSObject):
    _istanza = None

    @classmethod
    def sharedApplication(cls):
        if cls._istanza is None:
            cls._istanza = cls()
        return cls._istanza

    def __getattr__(self, nome):
        if nome.startswith(("set", "activate", "orderFront", "terminate")):
            return lambda *a: setattr(self, "ultimo", nome)
        raise AttributeError(nome)

    def keyWindow(self):
        return None
