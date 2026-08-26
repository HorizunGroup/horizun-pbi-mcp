"""Un helper que no responde jamas. Existe para probar que se le termina.

No imprime nada en stdout a proposito: el padre no debe poder confundir un
proceso colgado con una respuesta vacia.
"""
import sys
import time

if __name__ == "__main__":
    sys.stdin.read()
    while True:
        time.sleep(60)
