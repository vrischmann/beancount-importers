# Beancount Importers

This repository contains importers for [Beancount](http://furius.ca/beancount) which are capable of processing CSV exports from two french banks:

* [Fortuneo](https://fortuneo.fr)
* [Crédit Mutuel](https://creditmutuel.fr)

This is _not_ a tool that automically fetches CSV exports from the bank: you need to download the CSV files yourself.

## Installation

Since I have no idea yet how to package a Python application properly I can only recommend to clone the repository:

    git clone https://github.com/vrischmann/beancount-importers.git importers

# Usage

Put the following in a `config.py` file:

```python
from importers import fortuneo
from impoters import ccm

CONFIG = [
    fortuneo.Importer('Assets:Fortuneo:Checking', 'Assets:Fortuneo:Savings'),
    ccm.Importer('Assets:CCM:Checking')
]
```

Then:

    bean-extract config.py $HOME/Downloads/HistoriqueOperations_XXXX.zip > fortuneo.beancount
    bean-extract config.py $HOME/Downloads/0123456789.csv > ccm.beancount

Now you can integrate theses file into your main beancount ledger.