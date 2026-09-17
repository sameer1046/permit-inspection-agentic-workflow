import os, sys, tarfile

for path, directories, files in os.walk('.'):
    for f in files:
        if f.endswith(".tgz"):
            print(f)
            tar = tarfile.open(os.path.join(path,f), 'r:gz')
            tar.extractall(path=path)
            tar.close()
            os.remove(os.path.join(path,f))

