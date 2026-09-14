# PolymarketEvent attributes that are actually read in polymarket/__init__.py
# These are the only fields needed for the dispatcher to function correctly
import os
import logging
from typing import Any
from dotenv import load_dotenv
from types import SimpleNamespace

class SlimmedPolymarketEvent(SimpleNamespace):
    """A slimmed-down version of PolymarketEvent with a to_dict method."""
    
    def to_dict(self):
        """Convert the SimpleNamespace back to a dictionary."""
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, list):
                result[key] = [
                    item.to_dict() if isinstance(item, SlimmedPolymarketEvent) else item
                    for item in value
                ]
            elif isinstance(value, SlimmedPolymarketEvent):
                result[key] = value.to_dict()
            elif isinstance(value, SimpleNamespace):
                result[key] = vars(value)
            else:
                result[key] = value
        return result


# Market-level attributes (within event.markets list)
ATTRS = [
    'slug',
    'clobTokenIds',
    'question',
    'outcomes',
    'eventStartTime',
    'startDate',
    'startDateIso',
    'endDate',
    'endDateIso',
    'ticker',
    'title',
    'resolutionSource',
    'active',
    'closed',
    'negRisk',
    'orderPriceMinTickSize',
    'conditionId',
]

ALL_READ_ATTRIBUTES =  ATTRS
READ_ATTRIBUTES_SET = set(ALL_READ_ATTRIBUTES)

# Types considered primitives that can be removed if not in READ_ATTRIBUTES_SET
PRIMITIVE_TYPES = (str, int, float, bool)

if load_dotenv():
    protected_atts_from_env = set(os.getenv('POLYMARKET_PROTECTED_ATTRIBUTES', '').split(','))
    if protected_atts_from_env:
        logging.info(f"[_mem_slim] Adding protected attributes from environment: {protected_atts_from_env}")
        READ_ATTRIBUTES_SET.update(protected_atts_from_env)

def traverse_and_slim(event) -> Any:
    """
    Traverse a PolymarketEvent and create a slim dummy object containing only
    the attributes specified in ALL_READ_ATTRIBUTES.
    
    Creates SimpleNamespace objects dynamically, preserving nested structure
    (objects within lists, etc.) but stripping out primitive attributes not in
    the allowed set. Empty lists/dicts resulting from this process are removed.
    """
    def recursively_slim(obj):
        # Handle primitive types - return as-is
        if isinstance(obj, PRIMITIVE_TYPES) or obj is None:
            return obj
        
        # Handle lists - recursively slim each item
        elif isinstance(obj, list):
            slimmed_list = []
            for item in obj:
                slimmed_item = recursively_slim(item)
                slimmed_list.append(slimmed_item)
            return slimmed_list
        
        # Handle dicts - only keep keys in READ_ATTRIBUTES_SET
        elif isinstance(obj, dict):
            result = {
                k: recursively_slim(v) 
                for k, v in obj.items() 
                if k in READ_ATTRIBUTES_SET
            }
            return result
        
        # Handle objects (dataclasses, etc.) - create dummy SlimmedPolymarketEvent
        else:
            dummy = SlimmedPolymarketEvent()
            
            for attr in dir(obj):
                if attr.startswith('_'):
                    continue
                
                value = getattr(obj, attr, None)
                
                if callable(value):
                    continue
                
                # If it's a primitive and NOT in our allowed set, skip it
                if isinstance(value, PRIMITIVE_TYPES) and attr not in READ_ATTRIBUTES_SET:
                    continue
                
                # Skip None values only if they're NOT in our allowed set
                # Attributes in READ_ATTRIBUTES_SET must always be preserved, even if None
                if value is None and attr not in READ_ATTRIBUTES_SET:
                    continue
                
                # Recursively slim the value
                slimmed_value = recursively_slim(value)
                
                # For lists and dicts, only set if they're not empty
                if isinstance(value, (list, dict)) and not slimmed_value:
                    continue
                
                # Set the attribute on the dummy
                setattr(dummy, attr, slimmed_value)
            
            return dummy
    
    return recursively_slim(event)
