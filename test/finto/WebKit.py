"""Finto WebKit: la WKWebView risponde come farebbe ponte.js."""
from Foundation import NSObject

WKInactiveSchedulingPolicyNone = 2
WKAudiovisualMediaTypeNone = 0
WKUserScriptInjectionTimeAtDocumentEnd = 1
WKNavigationActionPolicyAllow, WKNavigationActionPolicyCancel = 1, 0
WKNavigationTypeLinkActivated = 0


class _Generico(NSObject):
    def __getattr__(self, nome):
        if nome.startswith("set"):
            return lambda *a: None
        raise AttributeError(nome)

    def respondsToSelector_(self, sel):
        return True


class WKPreferences(_Generico):
    pass


class WKUserContentController(_Generico):
    def addUserScript_(self, s):
        self.__dict__.setdefault("script", []).append(s)


class WKContentWorld:
    @classmethod
    def pageWorld(cls):
        return "pagina"


class ErroreJS:
    def __init__(self, messaggio):
        self.messaggio = messaggio

    def userInfo(self):
        return {"WKJavaScriptExceptionMessage": self.messaggio}

    def localizedDescription(self):
        return "Errore JavaScript"


class WKWebViewConfiguration(_Generico):
    def init(self):
        self._p, self._u = WKPreferences(), WKUserContentController()
        return self

    def preferences(self):
        return self._p

    def userContentController(self):
        return self._u


class WKWebpagePreferences(_Generico):
    pass


class WKUserScript(_Generico):
    def initWithSource_injectionTime_forMainFrameOnly_(self, src, t, m):
        self.sorgente, self.momento = src, t
        return self


WKUserScriptInjectionTimeAtDocumentStart = 0


class WKWebView(_Generico):
    pagina = None             # funzione js → risultato, impostata dal banco di prova
    eseguiti = []
    asincrona = None          # funzione (corpo, argomenti) → (risultato, messaggio d'errore)
    differiti = None          # se è una lista, le risposte asincrone vi restano in attesa
    chiamate_async = []

    def callAsyncJavaScript_arguments_inFrame_inContentWorld_completionHandler_(self, corpo, argomenti,
                                                                               frame, mondo, gestore):
        WKWebView.chiamate_async.append((corpo, argomenti, mondo))
        risultato, errore = WKWebView.asincrona(corpo, argomenti)
        consegna = (lambda: gestore(risultato, ErroreJS(errore) if errore else None))
        if WKWebView.differiti is not None:
            WKWebView.differiti.append(consegna)
        else:
            consegna()

    def initWithFrame_configuration_(self, f, cfg):
        self.cfg = cfg
        return self

    differiti_js = None       # se è una lista, le risposte di evaluateJavaScript vi restano in attesa

    def evaluateJavaScript_completionHandler_(self, js, gestore):
        WKWebView.eseguiti.append(js)
        risultato = WKWebView.pagina(js) if WKWebView.pagina else None   # valutato ora, come in WebKit
        if gestore is None:
            return
        if WKWebView.differiti_js is not None:
            WKWebView.differiti_js.append(lambda: gestore(risultato, None))
        else:
            gestore(risultato, None)

    def loadRequest_(self, r):
        self.caricato = r.URL().s

    def reload(self):
        self.ricaricata = True

    def URL(self):
        return "https://music.youtube.com/"
