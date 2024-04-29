# Useful venv commands

* create a new venv:
  ```
  python -m venv venv
  ```
* install requirements
  ```
  pip install -r requirements.txt
  ```
* update requirements
  ```
  pip install -r requirements.txt --upgrade
  ```

* upgrade pip
```
  # on linux
  python -m pip install --upgrade pip
```
```
  # on Windows
  python.exe -m pip install --upgrade pip
```

# Useful Git commands
* create tags/versions on GitHub
    ```
    git tag <version_number>
    git push origin <version_number>
    ```
* remove files git repository (not the file system)
    ```
    git rm --cached <path of file to be removed from git repo.>
    git commit -m "Deleted file from repository only"
    git push
    ```
* cancel command:
    ```
    git restore --staged .
    ```
  