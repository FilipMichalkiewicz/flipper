from colorama import init
from application import Application

init(convert=True)

app: Application

def main():
    
    try:
        app = Application() 

        app.run()
    except KeyboardInterrupt:
        app.exit()

main()