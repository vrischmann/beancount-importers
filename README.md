# Beancount Importer for Fortuneo

This is an importer for [Beancount](http://furius.ca/beancount), specifically for the `bean-extract` command, which converts CSV exports from the french bank [Fortuneo](https://www.fortuneo.fr).

This is _not_ a tool that automically fetches CSV exports from the bank: you need to download the CSV files yourself.

## Installation

Since I have no idea yet how to package a Python application properly I can only recommend to clone the repository:

    git clone https://github.com/vrischmann/beancount-fortuneo.git fortuneo

# Usage

Put the following in a `config.py` file:

```python
import fortuneo

CONFIG = [
    fortuneo.Importer('Assets:Fortuneo:Checking', 'Assets:Fortuneo:Savings'),
]
```

Then:

    bean-extract config.py $HOME/Downloads/HistoriqueOperations_XXXX.zip > fortuneo.beancount

Now you can integrate that file into your main beancount ledger.
